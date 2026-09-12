"""Fresh exact absence and lost continuation; no daemon cleanup inference."""

import json
import os
import signal
import sys
import time

import pytest

pytest.importorskip("millforge")
from cli.test_cli_native_run_controls import fixtures
from millrace.contracts.controls import ControlRequest
from millrace.substrate._sqlite_run_controls import native_witness_digest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="exact native process/lifecycle qualification requires macOS",
)

def recovery_request(case, control):
    value = case.request(action="runs.resume", pause_id=control.pause_id).payload
    value["action"] = "runs.recover"
    runtime = case.runtime()
    try:
        revision = runtime.store.control_identity()["source_revision"]
    finally:
        runtime.close()
    value["target"].update(
        mode="retire_native_continuation",
        expected_source_revision=revision,
        owner_id=control.native["owner"]["owner_id"],
        witness_digest=native_witness_digest(control),
    )
    return ControlRequest.parse(json.dumps(value))


def current(case):
    runtime = case.runtime()
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        return state, next(iter(state.run_execution_controls.values()))
    finally:
        runtime.close()


def test_applied_owner_death_requires_fresh_retirement():
    case = fixtures.LiveCase("N12-N13", delay=1)
    try:
        case.wait(case.server.started.is_set)
        pause = case.request()
        response = case.invoke(pause)
        assert response.returncode == 0, (response.stdout, response.stderr)
        historical = json.loads(response.stdout)["data"]
        state, control = current(case)
        assert control.state == "paused"
        from dataclasses import replace

        from millrace.adapters.cli.session_records import completion_record
        from millrace.contracts.transition import RecordRunnerSessionCompletion
        from millrace.kernel.runner_sessions import completion_refusal

        session = next(iter(state.runner_sessions.values()))
        completion = completion_record(
            session=session,
            terminal_state="lost",
            exit_kind="lost",
            adapter_outcome_kind="unsupported",
            adapter_error_kind=None,
            evidence_digest=None,
            diagnostic_digest="sha256:" + ("0" * 64),
            cleanup_disposition="orphan_risk",
            redaction_policy_id="native-proof-local",
        )
        transition = RecordRunnerSessionCompletion(
            "held-loss-probe",
            run_ref=state.runs[session.run_id].run_ref,
            expected_state=session.state,
            completion=completion,
        )
        assert completion_refusal(state, transition) is None
        for change in (
            {"session_id": "wrong-session"},
            {"session_fencing_token": "stale-fence"},
            {"dispatch_generation": session.dispatch_generation + 1},
        ):
            assert (
                completion_refusal(
                    state, replace(transition, completion=replace(completion, **change))
                )
                is not None
            )
        refused = case.invoke(recovery_request(case, control))
        assert refused.returncode != 0 and "owner_not_absent" in refused.stderr
        os.kill(case.child.pid, signal.SIGKILL)
        case.child.wait(timeout=2)
        request = recovery_request(case, control)
        recovered = case.invoke(request)
        assert recovered.returncode == 0, (
            recovered.stdout,
            recovered.stderr,
            case.root,
        )
        after, retired = current(case)
        assert retired.state == "retired"
        assert after.runner_sessions[next(iter(state.runner_sessions))].state == "lost"
        assert after.runs == state.runs
        assert len(after.runner_sessions) == 1
        data = json.loads(recovered.stdout)["data"]
        assert data["results"][-1]["evidence"]["cleanup"] == "not_required"
        assert data["results"][-1]["evidence"]["daemon_cleanup"] == "unproved"
        runtime = case.runtime()
        try:
            identity = runtime.store.control_identity()
            rows = tuple(
                runtime.store._connection.execute("SELECT * FROM control_operations")
            )
            results = tuple(
                runtime.store._connection.execute("SELECT * FROM control_results")
            )
            history = runtime.store.control_history_records(
                run_id=request.payload["target"]["run_id"]
            )
            assert any(item["record"] == data["receipt"] for item in history)
            replay = runtime.store.show_operation(request)
            assert replay["receipt"] == data["receipt"]
            assert replay["results"] == data["results"]
            assert runtime.store.control_identity() == identity
            assert (
                tuple(
                    runtime.store._connection.execute(
                        "SELECT * FROM control_operations"
                    )
                )
                == rows
            )
            assert (
                tuple(
                    runtime.store._connection.execute("SELECT * FROM control_results")
                )
                == results
            )
            old = runtime.store.show_operation(pause)
            assert old["receipt"] == historical["receipt"]
            assert old["results"][-1]["stage"] == "aftermath"
            assert old["results"][-1]["evidence"]["continuation"] == "lost"
            assert (
                runtime.store.load_daemon_budget_epoch(
                    "native-proof-epoch"
                ).accepted_start_count
                == 1
            )
        finally:
            runtime.close()
        (case.root / "recovery-evidence.json").write_text(
            json.dumps(
                {"historical": historical, "recovery": data, "old_history": old},
                indent=2,
            )
        )
    finally:
        case.close()


class _RepeatingModelServer(fixtures.ModelServer):
    """Script only HTTP responses so the actual facade can run several holds."""

    def __init__(self, *_args):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.count = 0
        self.requests = []
        self.started = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(body)
                owner.count += 1
                owner.started.set()
                time.sleep(0.05)
                call = {
                    "id": f"call-{owner.count}",
                    "type": "function",
                    "function": {
                        "name": "write" if owner.count % 2 else "read",
                        "arguments": json.dumps(
                            {"path": "repeated.txt", "content": str(owner.count)}
                            if owner.count % 2
                            else {"path": "repeated.txt"}
                        ),
                    },
                }
                raw = json.dumps(
                    {
                        "model": body["model"],
                        "choices": [
                            {
                                "finish_reason": "tool_calls",
                                "message": {
                                    "role": "assistant",
                                    "content": "local repeated control proof",
                                    "tool_calls": [call],
                                },
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 3,
                            "completion_tokens": 2,
                            "total_tokens": 5,
                        },
                    }
                ).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()


@pytest.mark.parametrize("admissions", [2, 3, 4, 6])
def test_native_operation_retention_matches_admission_order(monkeypatch, admissions):
    """Actual repeated controls, then contradictory retained-set public refusals."""
    import sqlite3
    import subprocess
    import sys
    from copy import deepcopy
    from dataclasses import replace

    from millrace.contracts.controls import canonical_json

    monkeypatch.setattr(fixtures, "ModelServer", _RepeatingModelServer)
    case = fixtures.LiveCase("E-R1-retention-" + str(admissions))
    history, evidence = [], []
    try:
        case.wait(lambda: case.server.count >= 3)
        identity = None
        pause_id = None
        for index in range(admissions):
            request = case.request(
                action="runs.pause" if index % 2 == 0 else "runs.resume",
                pause_id=None if index % 2 == 0 else pause_id,
                # Reverse lexical order intentionally disagrees with admission.
                operation_id=f"{100 - index:08x}-0000-4000-8000-000000000000",
            )
            response = case.invoke(request)
            assert response.returncode == 0, (response.stdout, response.stderr)
            _, control = current(case)
            pause_id = control.pause_id
            assert control.state == ("paused" if index % 2 == 0 else "resumed")
            snapshot = control.native["snapshot"]
            live_identity = (
                control.native["attempt"],
                snapshot["session_id"],
                snapshot["invocation_id"],
            )
            identity = live_identity if identity is None else identity
            assert live_identity == identity
            key = canonical_json(list(request.key))
            history.append((key, deepcopy(snapshot["operations"][key])))
            assert snapshot["operations"] == dict(history[-2:])
            assert list(snapshot["operations"]) == sorted(dict(history[-2:]))
            replay = case.invoke(request)
            assert replay.returncode == 0, (replay.stdout, replay.stderr)
            assert current(case)[1] == control
            if index % 2 == 0:
                assert snapshot["parked"] and snapshot["active_effects"] == 0
                before = case.server.count
                time.sleep(0.08)
                assert case.server.count == before
            elif index + 1 < admissions:
                before = case.server.count
                case.wait(lambda: case.server.count > before)
        case.child.kill()
        case.child.wait(timeout=2)
        request = recovery_request(case, control)
        db = sqlite3.connect(case.paths.db_path)
        baseline = sqlite3.connect(":memory:")
        try:
            from millrace.substrate._sqlite_run_controls import (
                _control_record,
                _native_snapshot_history,
                _validate_native_evidence,
            )

            # Component counterpart: owner closes before signaling this intent.
            # Even at capacity, no new native admission permits no eviction.
            intent = _control_record(
                *db.execute(
                    "SELECT run_id,record_json FROM run_control_events "
                    "WHERE control_revision=?",
                    (control.control_revision - 1,),
                ).fetchone()
            )
            closed_native = deepcopy(dict(intent.native))
            closed_native.update(kind="aftermath", aftermath_reason="native_preempted")
            closed_native["snapshot"].update(
                state="closed",
                eligible=False,
                reason="native_preempted",
                sequence=closed_native["snapshot"]["sequence"] + 1,
                os_descendants=None,
                descendant_boundary="unknown_after_unqualified_entry",
            )
            closed = replace(intent, state="unknown", native=closed_native)
            _validate_native_evidence(closed_native)
            revision = db.execute(
                "SELECT source_revision FROM control_identity"
            ).fetchone()[0]
            receipt = {"action": "runs.pause" if admissions % 2 else "runs.resume"}
            _native_snapshot_history(
                db, closed, intent, receipt, revision, time.monotonic() + 4
            )
            closed_native["snapshot"]["operations"].pop(history[-2][0])
            with pytest.raises(ValueError):
                _native_snapshot_history(
                    db, closed, intent, receipt, revision, time.monotonic() + 4
                )
            db.backup(baseline)
            triggers = db.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
            variants = ["missing_previous", "rewritten_previous"]
            if admissions >= 3:
                variants.append("wrong_eviction")
            for variant in variants:
                baseline.backup(db)
                for name, _ in triggers:
                    db.execute('DROP TRIGGER "' + name + '"')
                record = json.loads(
                    db.execute(
                        "SELECT record_json FROM run_execution_controls"
                    ).fetchone()[0]
                )
                operations = record["native"]["snapshot"]["operations"]
                if variant == "rewritten_previous":
                    operations[history[-2][0]]["stage"] = "unknown"
                else:
                    del operations[history[-2][0]]
                    if variant == "wrong_eviction":
                        operations[history[-3][0]] = history[-3][1]
                raw = canonical_json(record)
                db.execute("UPDATE run_execution_controls SET record_json=?", (raw,))
                db.execute(
                    "UPDATE run_control_events SET record_json=? "
                    "WHERE control_revision=?",
                    (raw, control.control_revision),
                )
                rid = "native:" + str(control.control_revision)
                result = json.loads(
                    db.execute(
                        "SELECT result_json FROM control_results WHERE result_id=?",
                        (rid,),
                    ).fetchone()[0]
                )
                result["evidence"] = record["native"]
                db.execute(
                    "UPDATE control_results SET result_json=? WHERE result_id=?",
                    (canonical_json(result), rid),
                )
                for _, sql in triggers:
                    db.execute(sql)
                db.commit()
                before = list(db.iterdump())
                from millrace.substrate.errors import ControlOperationError

                with pytest.raises(
                    ControlOperationError, match="storage_contradiction"
                ):
                    current(case)
                public = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from millrace.adapters.cli.main import main; "
                        "raise SystemExit(main())",
                        "--json",
                        "--workspace",
                        str(case.paths.workspace_path),
                        "--bounded",
                        "runs",
                        "list",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=6,
                )
                assert public.returncode != 0
                # Bounded public reads intentionally redact the storage detail.
                assert json.loads(public.stderr)["code"] == "projection_unavailable"
                value = deepcopy(request.payload)
                value["target"]["witness_digest"] = native_witness_digest(
                    replace(control, native=record["native"])
                )
                recovery = case.invoke(ControlRequest.parse(json.dumps(value)))
                assert recovery.returncode != 0
                assert "storage_contradiction" in recovery.stderr
                assert list(db.iterdump()) == before
                evidence.append(
                    {
                        "variant": variant,
                        "bounded_read_exit": public.returncode,
                        "bounded_read_stderr": public.stderr,
                        "fresh_recovery_exit": recovery.returncode,
                        "fresh_recovery_stderr": recovery.stderr,
                        "all_database_rows_unchanged": True,
                    }
                )
            baseline.backup(db)
        finally:
            baseline.close()
            db.close()
        assert current(case)[1] == control
        (case.root / "retention-evidence.json").write_text(
            json.dumps(
                {
                    "admissions": admissions,
                    "actual_facade_same_invocation": True,
                    "reverse_lexical_operation_ids": True,
                    "history": history,
                    "variants": evidence,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        case.close()


def test_pending_owner_death_refuses_clean_retirement():
    case = fixtures.LiveCase("N11", delay=8)
    try:
        case.wait(case.server.started.is_set)
        pause = case.request()
        response = case.invoke(pause)
        assert response.returncode == 0, (response.stdout, response.stderr)
        initial = json.loads(response.stdout)["data"]
        state, control = current(case)
        assert control.state == "pause_pending"
        os.kill(case.child.pid, signal.SIGKILL)
        case.child.wait(timeout=2)
        recovered = case.invoke(recovery_request(case, control))
        assert recovered.returncode != 0 and "witness_unavailable" in recovered.stderr
        runtime = case.runtime()
        try:
            assert runtime.store.show_operation(pause)["receipt"] == initial["receipt"]
        finally:
            runtime.close()
    finally:
        case.close()


@pytest.mark.parametrize("phase", ["pending", "held"])
def test_public_stop_preserves_native_first_cancellation(phase):
    import subprocess
    import sys

    from cli.test_cli_daemon_control import request_for_target
    from millrace.adapters.cli.daemon_control import inspect_daemon

    case = fixtures.LiveCase("N08-N15-" + phase, delay=6.5 if phase == "pending" else 1)
    try:
        case.wait(case.server.started.is_set)
        pause = case.request()
        response = case.invoke(pause)
        assert response.returncode == 0, (response.stdout, response.stderr)
        initial = json.loads(response.stdout)["data"]
        observed = inspect_daemon(case.paths, deadline=time.monotonic() + 1.5)
        request = request_for_target(observed["target"])
        started = time.monotonic()
        stopped = subprocess.run(
            [
                sys.executable,
                "-c",
                "from millrace.adapters.cli.main import main; raise SystemExit(main())",
                "--json",
                "--workspace",
                str(case.paths.workspace_path),
                "daemon",
                "stop",
                "--request-json",
                json.dumps(request),
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        assert time.monotonic() - started < 2
        assert stopped.returncode == 0, (stopped.stdout, stopped.stderr)
        case.child.wait(timeout=8)
        state, control = current(case)
        assert control.state == "unknown"
        assert not (case.paths.workspace_path / "after.txt").exists()
        primary = [
            x for x in state.runner_session_cancellation_requests.values() if x.primary
        ]
        assert len(primary) == 1 and primary[0].reason == "daemon_shutdown"
        completion = next(iter(state.runner_session_completions.values()))
        assert completion.primary_cancellation_request_id == primary[0].request_id
        runtime = case.runtime()
        try:
            assert runtime.store.show_operation(pause)["receipt"] == initial["receipt"]
            budget = runtime.store.load_daemon_budget_epoch("native-proof-epoch")
            assert budget.accepted_start_count == 1
        finally:
            runtime.close()
        (case.root / "stop-evidence.json").write_text(
            json.dumps(
                {
                    "initial": initial,
                    "stop": json.loads(stopped.stdout),
                    "control": dict(control.native),
                    "primary": primary[0].reason,
                    "budget_starts": budget.accepted_start_count,
                },
                indent=2,
            )
        )
    finally:
        case.close()


@pytest.mark.parametrize("phase", ["pending", "held"])
def test_native_deadline_does_not_extend_hold_or_budget(phase):
    case = fixtures.LiveCase(
        "N09-" + phase,
        delay=6.5 if phase == "pending" else 0.5,
        timeout=5 if phase == "pending" else 3,
    )
    try:
        case.wait(case.server.started.is_set)
        request = case.request()
        response = case.invoke(request)
        assert response.returncode == 0, (response.stdout, response.stderr)
        initial = json.loads(response.stdout)["data"]
        case.child.wait(timeout=8)
        state, control = current(case)
        assert control.state == "unknown"
        assert not (case.paths.workspace_path / "after.txt").exists()
        runtime = case.runtime()
        try:
            assert (
                runtime.store.show_operation(request)["receipt"] == initial["receipt"]
            )
            epoch = runtime.store.load_daemon_budget_epoch("native-proof-epoch")
            assert epoch.accepted_start_count == 1
        finally:
            runtime.close()
        assert next(iter(state.runner_sessions.values())).state in {
            "interrupted",
            "failed",
            "lost",
        }
        assert control.native["aftermath_reason"] in {
            "native_preempted",
            "finalizing",
            "core_authority_preempted",
        }
        (case.root / "deadline-evidence.json").write_text(
            json.dumps(
                {
                    "initial": initial,
                    "control": dict(control.native),
                    "budget_starts": epoch.accepted_start_count,
                },
                indent=2,
            )
        )
    finally:
        case.close()


@pytest.mark.parametrize("pause_first", [True, False])
def test_actual_unsupported_owned_child_never_qualifies(pause_first):
    import shlex
    import sys

    # The native stock bash effect owns this real process. It is outside the
    # qualified profile; test teardown below is not product recovery evidence.
    program = (
        "import os,time;from pathlib import Path;"
        "Path('owned-child.pid').write_text(str(os.getpid()));time.sleep(30)"
    )
    command = "exec " + shlex.quote(sys.executable) + " -c " + shlex.quote(program)
    case = fixtures.LiveCase(
        "N07-N13-child",
        delay=1 if pause_first else 0.1,
        unsupported=("bash", {"command": command}),
    )
    child_identity = None
    try:
        case.wait(case.server.started.is_set)
        if pause_first:
            pause = case.request()
            response = case.invoke(pause)
            assert response.returncode == 0, (response.stdout, response.stderr)
            state, control = current(case)
            assert control.state == "paused"
            assert not (case.paths.workspace_path / "owned-child.pid").exists()
            response = case.invoke(
                case.request(action="runs.resume", pause_id=control.pause_id)
            )
            assert response.returncode == 0, (response.stdout, response.stderr)
        case.wait(lambda: (case.paths.workspace_path / "owned-child.pid").exists())
        pid = int((case.paths.workspace_path / "owned-child.pid").read_text())
        from millrace.adapters.cli.daemon_process import process_identity

        child_identity = process_identity(pid)
        assert child_identity["status"] == "live"
        refused = case.invoke(case.request())
        assert refused.returncode != 0 and "unqualified" in refused.stderr
        if pause_first:

            def unqualified():
                return current(case)[1].state == "unqualified"

            case.wait(unqualified)
            state, control = current(case)
            assert control.native["snapshot"]["os_descendants"] is None
            os.kill(case.child.pid, signal.SIGKILL)
            case.child.wait(timeout=2)
            assert process_identity(pid) == child_identity
            recovery = case.invoke(recovery_request(case, control))
            assert recovery.returncode != 0 and "witness_unavailable" in recovery.stderr
            observation = json.loads(recovery.stderr)["details"]["results"][0][
                "evidence"
            ]["owner_loss_observation"]
            assert observation["authority"] == "original_accepted_control_aftermath"
            assert observation["control_action"] == "runs.resume"
            assert observation["operation_key"] == list(control.operation_key)
            state, control = current(case)
            assert control.state == "unknown"
            assert next(iter(state.runner_sessions.values())).state == "lost"
            assert process_identity(pid) == child_identity
            (case.root / "child-evidence.json").write_text(
                json.dumps(
                    {
                        "identity": child_identity,
                        "control": dict(control.native),
                        "recovery_refusal": json.loads(recovery.stderr),
                        "fixture_cleanup_is_not_recovery": True,
                    },
                    indent=2,
                )
            )
    finally:
        case.close()
        if child_identity is not None:
            from millrace.adapters.cli.daemon_process import process_identity

            if process_identity(child_identity["pid"]) == child_identity:
                os.kill(child_identity["pid"], signal.SIGKILL)


def test_owner_death_after_native_deadline_before_core_poll_refuses_old_witness():
    # Keep the scripted response pending beyond the five-second caller bound,
    # so pause acceptance precedes the after-write even on a loaded machine.
    # The native deadline remains real and expires after verified quiescence.
    case = fixtures.LiveCase(
        "N09-N11-preemption-order", delay=6.5, timeout=12, deadline_trap=True
    )
    try:
        case.wait(case.server.started.is_set)
        request = case.request()
        response = case.invoke(request)
        assert response.returncode == 0, (response.stdout, response.stderr)
        case.wait(lambda: (case.root / "coordinator-parked").exists())
        case.wait(lambda: (case.root / "preemption-durable").exists())
        state, control = current(case)
        assert control.state == "unknown"
        assert control.native["aftermath_reason"] in {"native_preempted", "finalizing"}
        assert not state.runner_session_cancellation_requests
        assert not (case.paths.workspace_path / "after.txt").exists()
        os.kill(case.child.pid, signal.SIGKILL)
        case.child.wait(timeout=2)
        refused = case.invoke(recovery_request(case, control))
        assert refused.returncode != 0 and "witness_unavailable" in refused.stderr
        after, control = current(case)
        assert next(iter(after.runner_sessions.values())).state == "lost"
        assert control.state == "unknown"
        (case.root / "preemption-race-evidence.json").write_text(
            json.dumps(
                {
                    "control": dict(control.native),
                    "core_cancellation_before_death": False,
                    "recovery": json.loads(refused.stderr),
                    "barriers": (
                        "fixture scheduling delay; "
                        "actual native invalidation journal executed"
                    ),
                },
                indent=2,
            )
        )
    finally:
        case.close()


def test_lifecycle_stop_is_bounded_while_native_admission_and_database_contend():
    import sqlite3
    import subprocess
    import sys
    import threading

    from cli.test_cli_daemon_control import request_for_target
    from millrace.adapters.cli.daemon_control import inspect_daemon

    case = fixtures.LiveCase("N15-contention", delay=6.5, admission_trap=True)
    client = None
    try:
        case.wait(case.server.started.is_set)
        request = case.request()
        observed = inspect_daemon(case.paths, deadline=time.monotonic() + 1.5)
        stopped_request = request_for_target(observed["target"])
        client = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from millrace.adapters.cli.main import main; raise SystemExit(main())",
                "--json",
                "--workspace",
                str(case.paths.workspace_path),
                "runs",
                "pause",
                "--request-json",
                request.canonical,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        case.wait(lambda: (case.root / "admission-waiting").exists())
        runtime = case.runtime()
        database = runtime.paths.db_path
        runtime.close()
        locked = threading.Event()

        def contend():
            connection = sqlite3.connect(database)
            connection.execute("BEGIN IMMEDIATE")
            locked.set()
            time.sleep(0.3)
            connection.rollback()
            connection.close()

        writer = threading.Thread(target=contend)
        writer.start()
        assert locked.wait(1)
        start = time.monotonic()
        stopped = subprocess.run(
            [
                sys.executable,
                "-c",
                "from millrace.adapters.cli.main import main; raise SystemExit(main())",
                "--json",
                "--workspace",
                str(case.paths.workspace_path),
                "daemon",
                "stop",
                "--request-json",
                json.dumps(stopped_request),
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        elapsed = time.monotonic() - start
        writer.join(1)
        assert elapsed < 2
        assert stopped.returncode == 0, (stopped.stdout, stopped.stderr)
        assert not (case.root / "admission-release").exists()
        (case.root / "admission-release").write_text("release after stop accepted")
        stdout, stderr = client.communicate(timeout=6)
        (case.root / "contention-evidence.json").write_text(
            json.dumps(
                {
                    "stop_seconds": elapsed,
                    "stop": json.loads(stopped.stdout),
                    "native_stdout": stdout,
                    "native_stderr": stderr,
                },
                indent=2,
            )
        )
    finally:
        (case.root / "admission-release").write_text("fixture teardown")
        if client is not None and client.poll() is None:
            client.kill()
            client.communicate(timeout=2)
        case.close()


@pytest.mark.parametrize("phase", ["pending", "held"])
def test_stale_recovery_targets_cannot_record_loss(phase):
    from dataclasses import asdict
    from uuid import uuid4

    case = fixtures.LiveCase("N11-stale-" + phase, delay=9 if phase == "pending" else 1)
    try:
        case.wait(case.server.started.is_set)
        pause = case.request()
        assert case.invoke(pause).returncode == 0
        case.child.kill()
        case.child.wait(timeout=2)
        state, control = current(case)
        baseline = (
            asdict(control),
            state.runner_sessions.copy(),
            state.runner_session_completions.copy(),
        )
        mutations = [
            ("target", "expected_control_revision", 101),
            ("target", "run_generation", 101),
            ("target", "run_fencing_token", "stale-run-fence"),
            ("target", "last_dispatch_generation", 101),
            ("session", "session_fencing_token", "stale-session-fence"),
            ("session", "session_id", "wrong-native-session"),
            ("session", "dispatch_generation", 101),
            ("target", "expected_source_revision", 0),
            ("target", "owner_id", str(uuid4())),
            ("target", "witness_digest", "f" * 64),
            ("request", "pause_id", str(uuid4())),
            ("profile", "profile_digest", "f" * 64),
        ]
        evidence = []
        for section, field, value in mutations:
            request = recovery_request(case, control).payload
            group = (
                request
                if section == "request"
                else request["profile"]
                if section == "profile"
                else request["target"]["expected_session"]
                if section == "session"
                else request["target"]
            )
            group[field] = value
            if field in {"dispatch_generation", "last_dispatch_generation"}:
                request["target"]["last_dispatch_generation"] = value
                request["target"]["expected_session"]["dispatch_generation"] = value
            response = case.invoke(ControlRequest.parse(json.dumps(request)))
            assert response.returncode != 0, (section, field, response.stdout)
            state, actual = current(case)
            assert (
                asdict(actual),
                state.runner_sessions,
                state.runner_session_completions,
            ) == baseline, (section, field)
            evidence.append(
                {
                    "field": section + "." + field,
                    "response": json.loads(response.stderr),
                }
            )
        (case.root / "stale-recovery-evidence.json").write_text(
            json.dumps(evidence, indent=2)
        )
    finally:
        case.close()


def test_unsafe_retirement_refusal_reconciles_original_pause_after_caller_loss(
    monkeypatch,
):
    from millrace.adapters.cli import run_controls
    from millrace.substrate.errors import ControlOperationError

    case = fixtures.LiveCase("N11-loss-observation-replay", delay=9)
    try:
        case.wait(case.server.started.is_set)
        pause = case.request()
        assert case.invoke(pause).returncode == 0
        case.child.kill()
        case.child.wait(timeout=2)
        _, control = current(case)
        request = recovery_request(case, control)
        finish = run_controls._finish_observed_native_loss

        def disconnect(*args):
            raise ControlOperationError("fixture_caller_lost_after_durable_observation")

        monkeypatch.setattr(run_controls, "_finish_observed_native_loss", disconnect)
        runtime = case.runtime()
        try:
            with pytest.raises(ControlOperationError, match="fixture_caller_lost"):
                run_controls.recover_native_control(
                    runtime, request, time.monotonic() + 4
                )
            receipt = runtime.store.show_operation(request)
            assert receipt["receipt"]["accepted"] is False
            assert receipt["receipt"]["reason_code"].endswith("loss_observed")
            assert (
                runtime.store.resolve_operation(request)["receipt"]
                == receipt["receipt"]
            )
        finally:
            runtime.close()
        state, control = current(case)
        assert control.state == "unknown" and not state.runner_session_completions
        monkeypatch.setattr(run_controls, "_finish_observed_native_loss", finish)
        response = case.invoke(request)
        assert response.returncode != 0
        result = json.loads(response.stderr)
        assert "Owner-loss aftermath was recorded" in result["message"]
        state, control = current(case)
        assert next(iter(state.runner_sessions.values())).state == "lost"
        assert len(state.runner_session_completions) == 1
        runtime = case.runtime()
        try:
            replay = runtime.store.show_operation(request)
            assert replay["receipt"] == receipt["receipt"]
            assert replay["results"] == receipt["results"]
            original = runtime.store.show_operation(pause)
            linked = original["results"][-1]["evidence"]
            assert linked == replay["results"][0]["evidence"]["owner_loss_observation"]
            assert linked["authority"] == "original_accepted_control_aftermath"
            assert linked["control_action"] == "runs.pause"
            assert (
                runtime.store.load_daemon_budget_epoch(
                    "native-proof-epoch"
                ).accepted_start_count
                == 1
            )
        finally:
            runtime.close()
        (case.root / "loss-observation-replay.json").write_text(
            json.dumps(
                {
                    "retirement_refusal": replay,
                    "original_pause": original,
                    "response": result,
                    "fixture_boundary": (
                        "source exception after durable SQL commit "
                        "before session completion"
                    ),
                },
                indent=2,
            )
        )
    finally:
        case.close()


@pytest.mark.parametrize("phase", ["held", "retired", "resumed"])
def test_corrupt_native_witness_refuses_reads_and_fresh_recovery(phase):
    """Real evidence, then controlled disk corruption with triggers restored."""
    import sqlite3
    from copy import deepcopy
    from dataclasses import replace

    from millrace.contracts.controls import canonical_json
    from millrace.substrate._sqlite_run_controls import load_run_controls
    from millrace.substrate.errors import ControlOperationError

    case = fixtures.LiveCase("E-R1-corrupt-" + phase, delay=1)
    evidence = []
    try:
        case.wait(case.server.started.is_set)
        pause = case.request()
        assert case.invoke(pause).returncode == 0
        state, held = current(case)
        assert held.native["snapshot"]["session_id"] != next(
            iter(state.runner_sessions)
        )
        retained_request = recovery_request(case, held)
        if phase == "resumed":
            assert (
                case.invoke(
                    case.request(action="runs.resume", pause_id=held.pause_id)
                ).returncode
                == 0
            )
        case.child.kill()
        case.child.wait(timeout=2)
        if phase == "retired":
            assert case.invoke(recovery_request(case, held)).returncode == 0
        _, control = current(case)
        # Resumed history is read/corruption evidence. Its formerly held target
        # cannot authorize retirement, even if normal work has already completed.
        request = (
            retained_request if phase == "resumed" else recovery_request(case, control)
        )
        db = sqlite3.connect(case.paths.db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute("UPDATE run_control_events SET record_json=record_json")
            db.rollback()
            tables = [
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            original = {
                table: db.execute("SELECT * FROM " + table).fetchall()
                for table in tables
            }
            # Phase-validator component check, not a runtime retry path:
            # a separately authorized later Core attempt has its own native hold.
            from millrace.substrate._sqlite_run_controls import _native_snapshot_history

            if phase == "held":
                resume_native = deepcopy(dict(control.native))
                resume_native["kind"] = "intent"
                resume_control = replace(
                    control,
                    state="resume_pending",
                    operation_key=(
                        *control.operation_key[:4],
                        "fresh-resume-operation",
                    ),
                    native=resume_native,
                )
                _native_snapshot_history(
                    db,
                    resume_control,
                    control,
                    {"action": "runs.resume"},
                    db.execute(
                        "SELECT source_revision FROM control_identity"
                    ).fetchone()[0],
                    time.monotonic() + 4,
                )
                resume_native["snapshot"]["operations"] = {}
                with pytest.raises(ValueError):
                    _native_snapshot_history(
                        db,
                        resume_control,
                        control,
                        {"action": "runs.resume"},
                        0,
                        time.monotonic() + 4,
                    )
            # Retained pre-signal intent plus a source-component closed snapshot:
            # unknown aftermath must not invent the un-signaled native key.
            from millrace.substrate._sqlite_run_controls import (
                _control_record,
                _validate_native_evidence,
            )

            intent_row = db.execute(
                "SELECT run_id,record_json FROM run_control_events "
                "WHERE control_revision=1"
            ).fetchone()
            intent = _control_record(*intent_row)
            closed_native = deepcopy(dict(intent.native))
            closed_native.update(kind="aftermath", aftermath_reason="native_preempted")
            closed_native["snapshot"].update(
                state="closed",
                eligible=False,
                reason="native_preempted",
                sequence=closed_native["snapshot"]["sequence"] + 1,
                os_descendants=None,
                descendant_boundary="unknown_after_unqualified_entry",
            )
            closed = replace(intent, state="unknown", native=closed_native)
            _validate_native_evidence(closed_native)
            _native_snapshot_history(
                db, closed, intent, {"action": "runs.pause"}, 0, time.monotonic() + 4
            )
            closed_native["snapshot"]["pause_id"] = intent.pause_id
            with pytest.raises(ValueError):
                _native_snapshot_history(
                    db,
                    closed,
                    intent,
                    {"action": "runs.pause"},
                    0,
                    time.monotonic() + 4,
                )
            fresh = deepcopy(dict(control.native))
            fresh.update(
                kind="intent",
                attempt={**fresh["attempt"], "session_id": "later-core-session"},
            )
            fresh["owner"] = {
                **fresh["owner"],
                "owner_id": "00000000-0000-0000-0000-000000000003",
            }
            fresh["snapshot"].update(
                owner_id=fresh["owner"]["owner_id"],
                session_id="00000000-0000-0000-0000-000000000004",
                invocation_id=fresh["snapshot"]["invocation_id"] + 1,
                state="running",
                pause_id=None,
                pending=None,
                operations={},
                sequence=0,
                parked=False,
            )
            next_control = replace(control, state="pause_pending", native=fresh)
            previous_control = replace(control, state="resumed")
            _native_snapshot_history(
                db,
                next_control,
                previous_control,
                {"action": "runs.pause"},
                0,
                time.monotonic() + 4,
            )
            fresh["snapshot"]["pause_id"] = control.pause_id
            with pytest.raises(ValueError):
                _native_snapshot_history(
                    db,
                    next_control,
                    previous_control,
                    {"action": "runs.pause"},
                    0,
                    time.monotonic() + 4,
                )
            triggers = db.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
            baseline = sqlite3.connect(":memory:")
            db.backup(baseline)
            operation_key = canonical_json(
                list(pause.key if phase == "retired" else control.operation_key)
            )
            variants = [
                "pause_id",
                "session_id",
                "invocation_id",
                "operation_key",
                "operation_digest",
                "operation_stage",
                "operation_sequence",
                "operation_sequence_backwards",
                "pending",
                "sequence",
                "intent_session",
                "intent_invocation",
            ]
            if phase == "resumed":
                variants.append("future_resume")
            for variant in variants:
                baseline.backup(db)
                for name, _ in triggers:
                    db.execute('DROP TRIGGER "' + name + '"')
                row = db.execute(
                    "SELECT run_id,record_json FROM run_execution_controls"
                ).fetchone()
                record = json.loads(row[1])
                if variant.startswith("intent_"):
                    record = json.loads(
                        db.execute(
                            "SELECT record_json FROM run_control_events "
                            "WHERE control_revision=1"
                        ).fetchone()[0]
                    )
                if variant == "future_resume":
                    record = json.loads(
                        db.execute(
                            "SELECT record_json FROM run_control_events "
                            "WHERE control_revision=2"
                        ).fetchone()[0]
                    )
                historical = variant.startswith("intent_") or variant == "future_resume"
                snapshot = record["native"]["snapshot"]
                if variant == "future_resume":
                    snapshot["operations"][operation_key] = dict(
                        control.native["snapshot"]["operations"][operation_key]
                    )
                elif variant in {"pause_id", "session_id", "intent_session"}:
                    snapshot[
                        "session_id" if variant == "intent_session" else variant
                    ] = "00000000-0000-0000-0000-000000000001"
                elif variant in {"invocation_id", "intent_invocation"}:
                    snapshot["invocation_id"] += 1
                elif variant == "operation_key":
                    key = list(pause.key)
                    key[-1] = "00000000-0000-0000-0000-000000000002"
                    snapshot["operations"][canonical_json(key)] = snapshot[
                        "operations"
                    ].pop(operation_key)
                elif variant == "operation_digest":
                    snapshot["operations"][operation_key]["digest"] = "f" * 64
                elif variant == "operation_stage":
                    snapshot["operations"][operation_key]["stage"] = "unknown"
                elif variant == "operation_sequence":
                    snapshot["operations"][operation_key]["sequence"] = (
                        snapshot["sequence"] + 1
                    )
                elif variant == "operation_sequence_backwards":
                    snapshot["operations"][operation_key]["sequence"] = 0
                elif variant == "pending":
                    snapshot["pending"] = operation_key
                else:
                    snapshot["sequence"] = 0
                raw = canonical_json(record)
                db.execute(
                    "UPDATE run_control_events SET record_json=? "
                    "WHERE run_id=? AND control_revision=?",
                    (raw, row[0], record["control_revision"]),
                )
                if not historical:
                    db.execute(
                        "UPDATE run_execution_controls SET record_json=? "
                        "WHERE run_id=?",
                        (raw, row[0]),
                    )
                if not variant.startswith("intent_"):
                    rid = "native:" + str(record["control_revision"])
                    result = json.loads(
                        db.execute(
                            "SELECT result_json FROM control_results WHERE result_id=?",
                            (rid,),
                        ).fetchone()[0]
                    )
                    result["evidence"] = record["native"]
                    db.execute(
                        "UPDATE control_results SET result_json=? WHERE result_id=?",
                        (canonical_json(result), rid),
                    )
                for _, sql in triggers:
                    db.execute(sql)
                db.commit()
                corrupted = {
                    table: db.execute("SELECT * FROM " + table).fetchall()
                    for table in tables
                }
                with pytest.raises(
                    ControlOperationError, match="storage_contradiction"
                ):
                    load_run_controls(db)
                # Construct the fresh request from the simulated bytes without
                # using a product loader that correctly refuses their authority.
                value = deepcopy(request.payload)
                if not historical:
                    value["target"]["witness_digest"] = native_witness_digest(
                        replace(control, native=record["native"])
                    )
                response = case.invoke(ControlRequest.parse(json.dumps(value)))
                assert (
                    response.returncode != 0
                    and "storage_contradiction" in response.stderr
                ), (
                    variant,
                    response.stdout,
                    response.stderr,
                )
                assert {
                    table: db.execute("SELECT * FROM " + table).fetchall()
                    for table in tables
                } == corrupted
                evidence.append(
                    {
                        "variant": variant,
                        "read_refused": True,
                        "public_recovery_exit": response.returncode,
                        "stderr": response.stderr,
                        "no_database_rows_changed": True,
                    }
                )
            baseline.backup(db)
            baseline.close()
            assert {
                table: db.execute("SELECT * FROM " + table).fetchall()
                for table in tables
            } == original
        finally:
            db.close()
        _, restored = current(case)
        assert restored == control
        (case.root / "witness-corruption-evidence.json").write_text(
            json.dumps(
                {"phase": phase, "sql_immutability": "PASS", "variants": evidence},
                indent=2,
            )
            + "\n"
        )
    finally:
        case.close()
