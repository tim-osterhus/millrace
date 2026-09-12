"""Actual native facade in the public daemon, with scripted external HTTP."""

import json

import pytest

pytest.importorskip("millforge")
import sys

from support.native_proof import fixtures

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="native owner qualification requires macOS"
)


def test_public_native_started_hold_resume():
    case = fixtures.LiveCase("N01", delay=1)
    try:
        case.wait(case.server.started.is_set)
        assert (case.paths.workspace_path / "before.txt").read_text() == "before"
        request = case.request()
        runtime = case.runtime()
        try:
            initial_state = runtime.store.load_runtime_state(runtime.cas_store)
            initial_sessions = initial_state.runner_sessions.copy()
            initial_run = next(iter(initial_state.runs.values()))
            initial_budget = runtime.store.load_daemon_budget_epoch(
                "native-proof-epoch"
            )
        finally:
            runtime.close()
        result = case.invoke(request)
        assert result.returncode == 0, (result.stdout, result.stderr, case.root)
        data = json.loads(result.stdout)["data"]
        runtime = case.runtime()
        try:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            control = next(iter(state.run_execution_controls.values()))
            assert control.state == "paused", data
            assert not (case.paths.workspace_path / "after.txt").exists()
            before = state.runner_sessions.copy()
            assert before == initial_sessions
            assert next(iter(state.runs.values())) == initial_run
            held_budget = runtime.store.load_daemon_budget_epoch("native-proof-epoch")
            for field in (
                "budget_id",
                "selected_plan_ref",
                "max_invocations",
                "started_at",
                "wall_deadline",
                "accepted_start_count",
                "cumulative_input_tokens",
                "cumulative_output_tokens",
                "cumulative_total_tokens",
            ):
                assert getattr(held_budget, field) == getattr(initial_budget, field)
            assert control.native["snapshot"]["parked"] is True
            assert control.native["snapshot"]["invalidation_pending"] is False
        finally:
            runtime.close()
        result = case.invoke(
            case.request(action="runs.resume", pause_id=control.pause_id)
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        case.wait(lambda: (case.paths.workspace_path / "after.txt").exists())
        assert (case.paths.workspace_path / "after.txt").read_text() == "after"
        case.child.wait(timeout=8)
        runtime = case.runtime()
        try:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            assert len(state.runner_session_completions) == 1
            assert next(iter(state.runner_sessions.values())).state == "completed"
            assert next(iter(state.runs.values())).run_ref == initial_run.run_ref
            assert set(state.runner_sessions) == set(before)
            assert len(state.runner_sessions) == 1
        finally:
            runtime.close()
    finally:
        case.close()


def test_public_slow_pending_replay_conflict_and_client_restart():
    import time

    from millrace.contracts.controls import ControlRequest

    case = fixtures.LiveCase("N03-N04-N06-N14", delay=6.5)
    try:
        case.wait(case.server.started.is_set)
        request = case.request()
        started = time.monotonic()
        response = case.invoke(request)
        assert time.monotonic() - started < 5
        assert response.returncode == 0, (response.stdout, response.stderr)
        initial = json.loads(response.stdout)["data"]
        assert initial["receipt"]["disposition"] == "accepted_pending"
        assert initial["results"][-1]["stage"] == "pending"
        assert not (case.paths.workspace_path / "after.txt").exists()
        runtime = case.runtime()
        try:
            resolved = runtime.store.resolve_operation(request)
            assert resolved["receipt"]["accepted"]
        finally:
            runtime.close()
        changed = ControlRequest.parse(
            json.dumps({**request.payload, "reason": "changed identity content"})
        )
        conflict = case.invoke(changed)
        assert conflict.returncode != 0 and "conflict" in conflict.stderr

        def held():
            runtime = case.runtime()
            try:
                return (
                    next(
                        iter(
                            runtime.store.load_runtime_state(
                                runtime.cas_store
                            ).run_execution_controls.values()
                        )
                    ).state
                    == "paused"
                )
            finally:
                runtime.close()

        case.wait(held)
        later = case.invoke(request)
        assert later.returncode == 0, (later.stdout, later.stderr)
        settled = json.loads(later.stdout)["data"]
        assert settled["receipt"] == initial["receipt"]
        assert settled["results"][-1]["stage"] == "applied"
        resume = case.invoke(
            case.request(action="runs.resume", pause_id=initial["receipt"]["pause_id"])
        )
        assert resume.returncode == 0, (resume.stdout, resume.stderr)
        case.wait(lambda: (case.paths.workspace_path / "after.txt").exists())
        (case.root / "control-evidence.json").write_text(
            json.dumps(
                {
                    "request": request.payload,
                    "initial": initial,
                    "settled": settled,
                    "resumed": json.loads(resume.stdout),
                },
                indent=2,
            )
        )
    finally:
        case.close()


def test_real_stock_tool_inflight_past_caller_deadline():
    import time

    case = fixtures.LiveCase("N05", delay=0.1, tool_lock=9)
    try:
        case.wait(case.server.started.is_set)
        time.sleep(0.3)
        request = case.request()
        started = time.monotonic()
        response = case.invoke(request)
        assert time.monotonic() - started < 5
        assert response.returncode == 0, (response.stdout, response.stderr)
        data = json.loads(response.stdout)["data"]
        assert data["results"][-1]["stage"] == "pending"
        runtime = case.runtime()
        try:
            control = next(
                iter(
                    runtime.store.load_runtime_state(
                        runtime.cas_store
                    ).run_execution_controls.values()
                )
            )
            assert control.native["snapshot"]["active_effects"] == 1
            assert control.native["snapshot"]["effect_counts"]["tool"] == 3
        finally:
            runtime.close()

        def held():
            runtime = case.runtime()
            try:
                return (
                    next(
                        iter(
                            runtime.store.load_runtime_state(
                                runtime.cas_store
                            ).run_execution_controls.values()
                        )
                    ).state
                    == "paused"
                )
            finally:
                runtime.close()

        case.wait(held)
        assert (case.paths.workspace_path / "after.txt").read_text() == "after"
        settled = case.invoke(request)
        assert settled.returncode == 0
        assert json.loads(settled.stdout)["data"]["results"][-1]["stage"] == "applied"
        (case.root / "control-evidence.json").write_text(
            json.dumps(
                {"initial": data, "settled": json.loads(settled.stdout)}, indent=2
            )
        )
    finally:
        case.close()


def test_native_history_corruption_and_bounded_projection():
    import sqlite3
    import subprocess
    import sys

    from millrace.substrate._sqlite_run_controls import load_run_controls
    from millrace.substrate.errors import ControlOperationError

    case = fixtures.LiveCase("N16", delay=1)
    try:
        case.wait(case.server.started.is_set)
        request = case.request()
        response = case.invoke(request)
        assert response.returncode == 0, (response.stdout, response.stderr)
        run_id = request.payload["target"]["run_id"]
        shown = subprocess.run(
            [
                sys.executable,
                "-c",
                "from millrace.adapters.cli.main import main; raise SystemExit(main())",
                "--json",
                "--workspace",
                str(case.paths.workspace_path),
                "--bounded",
                "runs",
                "show",
                run_id,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert shown.returncode == 0, (shown.stdout, shown.stderr)
        record = json.loads(shown.stdout)["data"]["records"][0]
        assert record["native_control"]["control_state"] == "paused"
        assert record["native_control"]["fresh_admission_required"]
        assert record["quiescence_evidence"]["witness"]["active_effects"] == 0
        assert record["runner_activity"] == "unknown"
        assert record["blocking_reasons"] == []
        assert record["capability_profile"]["qualification_status"] == (
            "recorded_exact_profile_live_admission_required"
        )
        assert record["checkpoint_status"] == {
            "availability": "unavailable",
            "reason": "live_only_continuation",
            "checkpoint_digest": None,
            "scope": "historical_effect_witness",
            "continuation": "paused",
        }

        from dataclasses import replace

        from millrace.adapters.cli.projections import run_projection

        runtime = case.runtime()
        try:
            original = runtime.store.load_runtime_state(runtime.cas_store)
            for control_state, session_state, reason in (
                ("pause_pending", "running", None),
                ("paused", "running", None),
                ("resume_pending", "running", None),
                ("resumed", "running", None),
                ("unqualified", "running", "runner_pause_unsupported"),
                ("paused", "cancellation_requested", "run_cancellation_in_progress"),
                ("paused", "completed", "run_attempt_terminal"),
                ("unknown", "lost", "run_aftermath_unknown"),
            ):
                session_id = original.runs[run_id].current_session_id
                state = replace(
                    original,
                    run_execution_controls={
                        **original.run_execution_controls,
                        run_id: replace(
                            original.run_execution_controls[run_id], state=control_state
                        ),
                    },
                    runner_sessions={
                        **original.runner_sessions,
                        session_id: replace(
                            original.runner_sessions[session_id],
                            state=session_state,
                            ended_at=original.runner_sessions[session_id].started_at
                            if session_state in {"completed", "lost"}
                            else None,
                            cleanup_disposition=(
                                "orphan_risk"
                                if session_state == "lost"
                                else "complete"
                                if session_state == "completed"
                                else original.runner_sessions[
                                    session_id
                                ].cleanup_disposition
                            ),
                        ),
                    },
                )
                before = repr(state)
                projected = run_projection(runtime, state, run_id)
                assert projected["blocking_reasons"] == ([reason] if reason else [])
                assert (
                    projected["checkpoint_status"]["reason"] == "live_only_continuation"
                )
                assert projected["native_control"]["fresh_admission_required"] is True
                assert repr(state) == before
        finally:
            runtime.close()
        source = sqlite3.connect(case.paths.db_path)
        copy = sqlite3.connect(case.root / "corrupt.sqlite3")
        source.backup(copy)
        source.close()
        row = copy.execute(
            "SELECT run_id,record_json FROM run_execution_controls"
        ).fetchone()
        value = json.loads(row[1])
        value["native"]["snapshot"]["active_effects"] = 1
        from millrace.contracts.controls import canonical_json

        copy.execute(
            "UPDATE run_execution_controls SET record_json=? WHERE run_id=?",
            (canonical_json(value), row[0]),
        )
        copy.commit()
        with pytest.raises(ControlOperationError):
            load_run_controls(copy)
        copy.close()
        (case.root / "bounded-evidence.json").write_text(shown.stdout)
    finally:
        case.close()


def test_lost_requesting_client_keeps_exact_pending_identity():
    import subprocess
    import sys

    case = fixtures.LiveCase("N06", delay=6.5)
    client = None
    try:
        case.wait(case.server.started.is_set)
        request = case.request()
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

        def accepted():
            runtime = case.runtime()
            try:
                return runtime.store.show_operation(request)["receipt"] is not None
            finally:
                runtime.close()

        case.wait(accepted)
        client.kill()
        client.communicate(timeout=2)

        def applied():
            runtime = case.runtime()
            try:
                return (
                    runtime.store.show_operation(request)["results"][-1]["stage"]
                    == "applied"
                )
            finally:
                runtime.close()

        case.wait(applied)
        result = case.invoke(request)
        assert result.returncode == 0, (result.stdout, result.stderr)
        data = json.loads(result.stdout)["data"]
        assert data["receipt"]["request_digest"] == request.digest
        assert data["receipt"]["key"]["operation_id"] == request.payload["operation_id"]
        assert len(data["results"]) == 2
        (case.root / "lost-client-evidence.json").write_text(result.stdout)
    finally:
        if client is not None and client.poll() is None:
            client.kill()
            client.communicate(timeout=2)
        case.close()


def test_independent_admitted_run_progresses_while_a_held(monkeypatch):
    import subprocess
    import sys

    case = fixtures.LiveCase("N02", delay=1)
    try:
        case.wait(case.server.started.is_set)
        request = case.request()
        response = case.invoke(request)
        assert response.returncode == 0, (response.stdout, response.stderr)
        base = [
            sys.executable,
            "-c",
            "from millrace.adapters.cli.main import main; raise SystemExit(main())",
            "--json",
            "--workspace",
            str(case.paths.workspace_path),
        ]
        queued = subprocess.run(
            [
                *base,
                "queue",
                "enqueue",
                "prompt",
                "--payload-json",
                json.dumps({"prompt_id": "B", "body": "independent-B"}),
                "--input-id",
                "enqueue-B",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert queued.returncode == 0, (queued.stdout, queued.stderr)
        runtime = case.runtime()
        try:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            activation = next(
                item.activation_id
                for item in state.activations.values()
                if item.created_by_input_id == "enqueue-B"
            )
            budget_before = runtime.store.load_daemon_budget_epoch("native-proof-epoch")
        finally:
            runtime.close()
        from dataclasses import asdict

        from millrace.adapters.cli.run import run_bounded_execution_unit

        monkeypatch.setenv("CORE_NATIVE_PROOF_KEY", "local-script-only")
        runtime = case.runtime()
        try:
            completed = run_bounded_execution_unit(
                runtime, activation_id=activation, local_config_path=case.config
            )
        finally:
            runtime.close()
        assert completed.code == "observation_accepted", completed
        from millrace.adapters.cli.run_controls import _NATIVE_OWNERS

        assert not _NATIVE_OWNERS, "completed native owner retained in this process"
        assert (case.paths.workspace_path / "b-after.txt").read_text() == "after"
        assert not (case.paths.workspace_path / "after.txt").exists()
        runtime = case.runtime()
        try:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            assert (
                state.run_execution_controls[request.payload["target"]["run_id"]].state
                == "paused"
            )
            assert (
                runtime.store.load_daemon_budget_epoch(
                    "native-proof-epoch"
                ).accepted_start_count
                == budget_before.accepted_start_count
            )
            assert len(state.runner_sessions) == 2
        finally:
            runtime.close()
        (case.root / "independent-evidence.json").write_text(
            json.dumps(
                {
                    "enqueue": json.loads(queued.stdout),
                    "completed": asdict(completed),
                    "budget_starts": budget_before.accepted_start_count,
                },
                indent=2,
            )
        )
    finally:
        case.close()


def test_caller_profile_cannot_qualify_native_owner():
    from millrace.contracts.controls import ControlRequest

    case = fixtures.LiveCase("N07-profile", delay=1.5)
    try:
        case.wait(case.server.started.is_set)
        value = case.request().payload
        value["profile"]["profile_digest"] = "f" * 64
        request = ControlRequest.parse(json.dumps(value))
        response = case.invoke(request)
        assert response.returncode != 0
        runtime = case.runtime()
        try:
            history = runtime.store.show_operation(request)
            assert history["receipt"] is None or not history["receipt"]["accepted"]
            assert not runtime.store.load_runtime_state(
                runtime.cas_store
            ).run_execution_controls
        finally:
            runtime.close()
    finally:
        case.close()


def test_default_generated_native_identity_projection_through_completion(monkeypatch):
    """Real native effects, ordinary public enqueue IDs, and one full witness."""
    import threading
    from copy import deepcopy
    from types import SimpleNamespace

    from cli.test_cli_bounded_execution_unit import (
        AdmitPlan,
        InitializeWorkspace,
        SelectDefaultPlan,
        apply_accepted_input,
        empty_runtime_state,
        kernel_ping_context,
    )
    from cli.test_cli_plan_commands import _invoke
    from millrace.adapters.cli.projections import (
        page_records,
        run_projection,
        session_projection,
    )
    from millrace.contracts.public_projections import MAX_ITEM_BYTES, wire
    from millrace.substrate._sqlite_run_controls import native_witness_digest

    def ready(plan, fingerprint):
        state = empty_runtime_state()
        for item in (
            InitializeWorkspace("init"),
            AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
            SelectDefaultPlan("select", authority_fingerprint=fingerprint),
        ):
            state = apply_accepted_input(
                state, item, kernel_ping_context(item.input_id)
            )
        return state, fingerprint

    original_runtime = fixtures._runtime

    def queued_runtime(root, state):
        runtime = original_runtime(root, state)
        code, out, err = _invoke(
            [
                "--json",
                "--workspace",
                str(runtime.paths.workspace_path),
                "queue",
                "enqueue",
                "prompt",
                "--payload-json",
                json.dumps({"prompt_id": "native", "body": "Build the proof"}),
                "--input-id",
                "native-enqueue",
            ]
        )
        assert code == 0, (out, err)
        return runtime

    # Hold only the external scripted terminal response so resumed is observable.
    terminal_entered = threading.Event()
    release_terminal = threading.Event()
    original_server = fixtures.ModelServer

    def server(*args):
        result = original_server(*args)
        handler = result.http.RequestHandlerClass
        original_post = handler.do_POST

        def post(self):
            if result.count == 3:
                terminal_entered.set()
                assert release_terminal.wait(15)
            original_post(self)

        handler.do_POST = post
        return result

    monkeypatch.setattr(fixtures, "_ready_state_for_plan", ready)
    monkeypatch.setattr(fixtures, "_runtime", queued_runtime)
    monkeypatch.setattr(fixtures, "ModelServer", server)
    case = fixtures.LiveCase("size-default-native", delay=6.5)
    observed = {}
    try:
        case.wait(case.server.started.is_set)
        request = case.request()
        run_id = request.payload["target"]["run_id"]
        assert run_id.startswith(
            "cli:run.bounded:cli:run.bounded:claim:cli:queue.enqueue:"
        )
        assert len(run_id) > 140

        def observe(label):
            runtime = case.runtime()
            try:
                with runtime.store.read_transaction():
                    state = runtime.store.load_runtime_state(runtime.cas_store)
                    before = repr(state)
                    run = state.runs[run_id]
                    control = state.run_execution_controls[run_id]
                    row = run_projection(runtime, state, run_id)
                    session = session_projection(
                        runtime, state, state.runner_sessions[run.current_session_id]
                    )
                    assert row["runner_session"] == session
                    assert row["run_id"] == row["id"] == run.run_ref.run_id
                    assert row["claim_id"] == run.run_ref.claim_id
                    assert row["run_fencing_token"] == run.run_ref.fencing_token
                    assert row["plan_fingerprint"] == str(
                        run.run_ref.plan_ref.authority_fingerprint
                    )
                    assert (
                        row["expected_session"]["session_id"] == run.current_session_id
                    )
                    assert row["native_control"][
                        "witness_digest"
                    ] == native_witness_digest(control)
                    assert (
                        row["quiescence_evidence"]["witness"]
                        == control.native["snapshot"]
                    )
                    assert (
                        row["quiescence_evidence"]["scope"]
                        == "historical_effect_witness"
                    )
                    assert row["native_control"]["fresh_admission_required"] is True
                    native = row["execution_hold"]["native"]
                    assert "snapshot" not in native
                    assert native["snapshot_reference"] == {
                        "scope": "same_run_record",
                        "json_pointer": "/quiescence_evidence/witness",
                    }
                    reconstructed = deepcopy(native)
                    reconstructed.pop("snapshot_reference")
                    reconstructed["snapshot"] = row["quiescence_evidence"]["witness"]
                    assert reconstructed == control.native
                    assert repr(state) == before
                    assert len(wire(row)) <= MAX_ITEM_BYTES
                    assert len(wire(session)) <= MAX_ITEM_BYTES
                    page = page_records(
                        [row],
                        {
                            "identity": {
                                "workspace_id": "w",
                                "instance_id": "i",
                                "store_epoch": "e",
                            }
                        },
                        SimpleNamespace(page_size=50, cursor=None),
                        pin=1,
                        filters={},
                    )
                    assert page["records"] == [row]
                    observed[label] = row
                    return control.state
            finally:
                runtime.close()

        pending = case.invoke(request)
        assert pending.returncode == 0, (pending.stdout, pending.stderr)
        assert json.loads(pending.stdout)["data"]["results"][-1]["stage"] == "pending"
        assert observe("pending") == "pause_pending"

        def current_control():
            runtime = case.runtime()
            try:
                return runtime.store.load_runtime_state(
                    runtime.cas_store
                ).run_execution_controls[run_id]
            finally:
                runtime.close()

        case.wait(lambda: current_control().state == "paused")
        assert observe("held") == "paused"
        resumed = case.invoke(
            case.request(action="runs.resume", pause_id=current_control().pause_id)
        )
        assert resumed.returncode == 0, (resumed.stdout, resumed.stderr)
        case.wait(terminal_entered.is_set)
        assert observe("resumed") == "resumed"
        release_terminal.set()
        case.child.wait(timeout=10)
        observe("terminal")
        terminal = observed["terminal"]
        session = terminal["runner_session"]
        assert session["state"] == "completed"
        assert session["completion_persisted"] and session["application_persisted"]
        assert session["application_status"] == "applied"
        assert session["completion_input_status"] == "verified_payload_digest_and_trace"
        assert terminal["native_control"]["aftermath_reason"] == "unsupported_tool"
        assert terminal["native_control"]["daemon_cleanup"] == "unproved"
        assert (
            session["descendant_aftermath"]["boundary"]
            == "unknown_after_unqualified_entry"
        )
        assert terminal["control_receipt_result_references"]
        from cli.test_cli_projections import inventory

        before_files = inventory(case.paths.workspace_path)
        for command in (("runs", "show", run_id), ("runs", "list")):
            code, out, err = _invoke(
                [
                    "--json",
                    "--bounded",
                    "--workspace",
                    str(case.paths.workspace_path),
                    *command,
                ]
            )
            assert code == 0, (out, err)
            public_rows = json.loads(out)["data"]["records"]
            assert next(
                row
                for row in public_rows
                if row["kind"] == "run" and row["id"] == run_id
            ) == json.loads(wire(terminal))
        assert inventory(case.paths.workspace_path) == before_files
        # Restoring the former duplicate proves this is a size regression.
        redundant = deepcopy(terminal)
        native = redundant["execution_hold"]["native"]
        native.pop("snapshot_reference")
        native["snapshot"] = redundant["quiescence_evidence"]["witness"]
        assert len(wire(redundant)) > MAX_ITEM_BYTES
        assert len({row["budget_binding"] for row in observed.values()}) == 1
        assert len({row["run_id"] for row in observed.values()}) == 1
        (case.root / "projection-size-evidence.json").write_text(
            json.dumps(observed, indent=2)
        )
    finally:
        release_terminal.set()
        case.close()
