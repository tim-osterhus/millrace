"""Shared controls: behavior proofs, including durable legal aftermath."""

import json
import sys
from pathlib import Path

import pytest
from tests.router.support import command_graph_json, create_instance, start_run

from millrace.router import control
from millrace.router.records import CommandResult
from millrace.router.store import Store, file_lock, new_id


def _wait_for_cancel(poll):
    """Worker poll observes owner-set flags; it never drains the control queue."""
    import time
    from threading import Event

    deadline = time.monotonic() + 3
    while not poll():
        assert time.monotonic() < deadline
        Event().wait(0.005)
    return True


def test_control_idempotency_and_legal_aftermath(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    paused = start_run(instance, state_home=home)
    receipt = control.resume_plane(
        instance,
        str(paused["id"]),
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
        plane_id="main",
    )
    assert receipt["status"] == "applied"
    later = control.inspect_run(instance, str(paused["id"]), state_home=home)
    assert later["count"] == 2
    assert later["status"] == "paused"


def test_new_live_revision_routes_old_completion(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])

    def completed(*args: object) -> CommandResult:
        graph = json.loads(workflow.read_text())
        graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {"finish": "failed"}
        workflow.write_text(command_graph_json(graph))
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr("millrace.router.engine.invoke", completed)
    result = start_run(instance, state_home=home, edit_mode="live")
    assert result["status"] == "failed"
    assert result["invocations"][0]["revision_id"] != result["revision_id"]


def test_new_pending_revision_is_not_an_active_invocation(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    paused = start_run(instance, state_home=home)
    receipt = control.submit_revision(
        instance,
        str(paused["id"]),
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
    )
    assert receipt["status"] == "applied"


def test_new_live_holder_receives_durable_control(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    config = control.load_instance(instance, home)
    run = start_run(instance, state_home=home)
    with file_lock(config.state_dir / ".controller.lock"):
        # Existing baseline has no queue support: this is a behavior RED,
        # rather than an import of a module that does not exist yet.
        receipt = control.pause_run(
            instance, str(run["id"]), state_home=home, request_id=new_id()
        )
    assert receipt["status"] in {"queued", "refused"}
    assert receipt.get("code") != "pilot_unsupported"


def test_reconciliation_preserves_retained_completion(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "may_live", exit_code=0
        ),
    )
    paused = start_run(instance, state_home=home)
    receipt = control.reconcile_invocation(
        instance,
        str(paused["id"]),
        invocation_id=paused["invocations"][0]["id"],
        reason="verified",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
        plane_id="main",
    )
    assert receipt["status"] == "applied"
    assert (
        control.inspect_run(instance, str(paused["id"]), state_home=home)["transitions"]
        == []
    )


def test_new_instance_settings_can_change_after_terminal(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    start_run(instance, state_home=home)
    receipt = control.update_instance_config(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=2,
        settings={"env_allowlist": ["ROUTER_TEST_NAME"]},
    )
    assert receipt["status"] == "applied"


def test_new_deleted_store_orphan_release(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(b"", b"", "execution_failure", "unknown"),
    )
    paused = start_run(instance, state_home=home)
    config = control.load_instance(instance, home)
    config.store_path.unlink()
    receipt = control.release_project_hold(
        project_root=project,
        hold_id=paused["hold_id"],
        owner_instance_id=instance,
        owner_run_id=paused["id"],
        reason="verified",
        stopped=True,
        request_id=new_id(),
        state_home=home,
    )
    assert receipt["status"] == "applied"
    assert not (project / ".millrace-router-owner/owner.json").exists()


def test_signal_before_queued_start_is_not_lost(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router.engine import Signals

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    original = Signals.__enter__

    def interrupted(signals: Signals) -> Signals:
        original(signals)
        signals.count = 1
        return signals

    monkeypatch.setattr(Signals, "__enter__", interrupted)
    result = start_run(instance, state_home=home)
    assert result["status"] == "paused"
    assert result["count"] == 0
    assert result["controls"] == []  # legacy helper normalizes receipt history only


def test_exact_receipt_replay_and_fresh_explicit_intent(
    single_flow: tuple[Path, Path, Path],
) -> None:
    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = str(before["id"])
    request_id = new_id()
    receipt = control.resume_plane(
        instance,
        run,
        state_home=home,
        request_id=request_id,
        expected_state_version=before["state_version"],
        plane_id="main",
    )
    current = control.inspect_run(instance, run, state_home=home)
    assert current["count"] == 2 and current["status"] == "paused"
    assert (
        control.resume_plane(
            instance,
            run,
            state_home=home,
            request_id=request_id,
            expected_state_version=before["state_version"],
            plane_id="main",
        )
        == receipt
    )
    assert control.inspect_run(instance, run, state_home=home) == current
    with pytest.raises(RouterError, match="request_id_reused"):
        control.resume_plane(
            instance,
            run,
            state_home=home,
            request_id=request_id,
            expected_state_version=current["state_version"],
            plane_id="main",
        )
    stale_id = new_id()
    refused = control.resume_plane(
        instance,
        run,
        state_home=home,
        request_id=stale_id,
        expected_state_version=before["state_version"],
        plane_id="main",
    )
    assert refused["code"] == "stale_state_version"
    assert (
        control.inspect_run(instance, run, state_home=home)["state_version"]
        == current["state_version"]
    )
    assert (
        control.resume_plane(
            instance,
            run,
            state_home=home,
            request_id=stale_id,
            expected_state_version=before["state_version"],
            plane_id="main",
        )
        == refused
    )
    fresh = control.resume_plane(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=current["state_version"],
        plane_id="main",
    )
    assert fresh["status"] == "applied"
    assert control.inspect_run(instance, run, state_home=home)["count"] == 3


@pytest.mark.parametrize("operation", ["retry", "redirect"])
def test_manual_continuation_resolves_old_pause(
    flow: tuple[Path, Path, Path], operation: str
) -> None:
    workflow, project, home = flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "check"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = str(before["id"])
    if operation == "retry":
        receipt = control.retry_invocation(
            instance,
            run,
            invocation_id=before["invocations"][0]["id"],
            reason="repeat",
            state_home=home,
            request_id=new_id(),
            expected_state_version=before["state_version"],
            plane_id="main",
        )
    else:
        receipt = control.redirect_plane(
            instance,
            run,
            node_id="check",
            reason="checked",
            state_home=home,
            request_id=new_id(),
            expected_state_version=before["state_version"],
            plane_id="main",
        )
    assert receipt["status"] == "applied"
    after = control.inspect_run(instance, run, state_home=home)
    assert after["count"] == 2 and after["transitions"][0] == before["transitions"][0]
    assert after["retry_counts"]["main"] == (
        {"draft": 1} if operation == "retry" else {}
    )
    if operation == "retry":
        assert after["status"] == "paused"
        control.resume_plane(
            instance,
            run,
            state_home=home,
            request_id=new_id(),
            expected_state_version=after["state_version"],
            plane_id="main",
        )
        after = control.inspect_run(instance, run, state_home=home)
        assert after["count"] == 3
    assert after["status"] == "succeeded"
    assert control.inspect_run(instance, run, state_home=home) == after


def test_surviving_resume_is_ordered_before_direct_pause(
    single_flow: tuple[Path, Path, Path],
) -> None:
    from millrace.router.control_requests import append, envelope

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = str(before["id"])
    config = control.load_instance(instance, home)
    request = envelope(
        "resume_plane",
        instance,
        run,
        new_id(),
        before["state_version"],
        {},
        plane_id="main",
    )
    store = Store(config)
    append(store, request)  # surviving row from a departed holder, isolated fixture
    store.close()
    receipt = control.pause_run(instance, run, state_home=home, request_id=new_id())
    assert receipt["status"] == "applied"
    after = control.inspect_run(instance, run, state_home=home)
    assert after["count"] == 1 and after["status"] == "paused"
    assert [row["status"] for row in after["controls"]] == ["applied", "applied"]
    first = control.execute_request(request, state_home=home)
    assert first["action"] == "resumed"
    assert control.inspect_run(instance, run, state_home=home) == after
    control.resume_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=after["state_version"],
    )
    assert control.inspect_run(instance, run, state_home=home)["count"] == 2


def test_final_drain_promotes_same_holder_and_reopens(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router.control_requests import append, envelope
    from millrace.router.controller import Controller

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = str(before["id"])
    original = Controller.queue_state
    injected = False
    seen: list[object] = []

    def close(controller: Controller, state: str) -> None:
        nonlocal injected
        original(controller, state)
        if state == "closing" and not injected:
            injected = True
            append(
                controller.store,
                envelope(
                    "resume_plane",
                    instance,
                    run,
                    new_id(),
                    controller.store.run(run)["state_version"],
                    {},
                    plane_id="main",
                ),
            )

    def invocation(*args: object) -> CommandResult:
        config = control.load_instance(instance, home)
        assert (
            json.loads((config.state_dir / ".queue.json").read_text())["state"]
            == "open"
        )
        held = control.recover_run(
            instance,
            run,
            state_home=home,
            request_id=new_id(),
            expected_state_version=control.inspect_run(instance, run, state_home=home)[
                "state_version"
            ],
        )
        assert held["code"] == "controller_busy"
        stop = control.pause_run(instance, run, state_home=home, request_id=new_id())
        assert stop["status"] == "queued"
        seen.append(stop)
        args[4]()
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr(Controller, "queue_state", close)
    monkeypatch.setattr("millrace.router.engine.invoke", invocation)
    receipt = control.submit_revision(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
    )
    assert receipt["status"] == "applied" and seen
    after = control.inspect_run(instance, run, state_home=home)
    assert after["status"] == "paused" and after["count"] == 2
    assert all(row["status"] != "queued" for row in after["controls"])
    assert any(row.get("code") == "controller_busy" for row in after["controls"])


def test_corrupt_queue_does_not_escape_active_adapter(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3
    import threading

    from millrace.router.controller import Controller
    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}
    queued: list[str] = []
    raw = b'{"outcome":"READY"}'
    corrupted = threading.Event()
    original_poll = Controller.poll

    def poll(owner, active):
        if active:
            assert corrupted.wait(timeout=3)
        return original_poll(owner, active)

    monkeypatch.setattr(Controller, "poll", poll)

    def invocation(*args: object) -> CommandResult:
        run = str(ids["run_id"])
        first = control.pause_run(instance, run, state_home=home, request_id=new_id())
        second = control.cancel_run(instance, run, state_home=home, request_id=new_id())
        queued.extend([first["request_id"], second["request_id"]])
        connection = sqlite3.connect(control.load_instance(instance, home).store_path)
        connection.execute(
            "UPDATE controls SET expected_version=99 WHERE request_id=?", (queued[0],)
        )
        connection.commit()
        connection.close()
        assert args[4]() is False  # workers observe cancel flags, never drain controls
        corrupted.set()
        return CommandResult(raw, b"", "completed", "stopped", exit_code=0)

    monkeypatch.setattr("millrace.router.engine.invoke", invocation)
    with pytest.raises(RouterError, match="control_integrity"):
        control.start_run(
            instance,
            state_home=home,
            request_id=new_id(),
            expected_state_version=1,
            on_started=ids.update,
        )
    config = control.load_instance(instance, home)
    connection = sqlite3.connect(config.store_path)
    assert connection.execute("SELECT stdout,writer FROM completions").fetchone() == (
        raw,
        "stopped",
    )
    assert connection.execute("SELECT count(*) FROM transitions").fetchone()[0] == 0
    assert [
        row[0]
        for row in connection.execute(
            "SELECT status FROM controls WHERE request_id IN (?,?) ORDER BY seq", queued
        )
    ] == ["queued", "queued"]
    before = list(connection.iterdump())
    connection.close()
    with pytest.raises(RouterError, match="control_integrity"):
        control.inspect_run(instance, str(ids["run_id"]), state_home=home)
    connection = sqlite3.connect(config.store_path)
    assert list(connection.iterdump()) == before
    connection.close()
    assert (project / ".millrace-router-owner/owner.json").exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_foreground_recovery_and_queued_stop_requests(
    single_flow: tuple[Path, Path, Path], cancel: bool
) -> None:
    import os
    import subprocess
    import time

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = 3
    workflow.write_text(command_graph_json(graph))
    (workflow.parent / "scripts/draft.py").write_text(
        'import time\ntime.sleep(1.2)\nprint(\'{"outcome":"READY"}\')\n'
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    cli = sys.executable
    process = subprocess.Popen(
        [
            cli,
            "-m",
            "millrace.router.cli",
            "--state-home",
            str(home),
            "start",
            "--instance",
            instance,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        assert process.stdout is not None
        transmitted = json.loads(process.stdout.readline())
        ids = json.loads(process.stdout.readline())
        run = ids["run_id"]
        until = time.monotonic() + 3
        while True:
            active = control.inspect_run(instance, run, state_home=home)
            if (
                active["invocations"]
                and active["invocations"][-1]["status"] == "active"
            ):
                break
            assert time.monotonic() < until
            time.sleep(0.01)
        started = time.monotonic()
        for _ in range(12):
            busy = control.recover_run(
                instance,
                run,
                state_home=home,
                request_id=new_id(),
                expected_state_version=active["state_version"],
            )
            assert busy["status"] == "refused" and busy["code"] == "controller_busy"
        started = time.monotonic()
        pause = control.pause_run(instance, run, state_home=home, request_id=new_id())
        assert pause["status"] == "queued" and time.monotonic() - started < 0.5
        pending = [
            control.resume_run(
                instance,
                run,
                state_home=home,
                request_id=new_id(),
                expected_state_version=1,
            ),
            control.submit_revision(
                instance,
                run,
                state_home=home,
                request_id=new_id(),
                expected_state_version=1,
            ),
            control.update_instance_config(
                instance,
                state_home=home,
                request_id=new_id(),
                expected_state_version=1,
                settings={"env_allowlist": []},
            ),
        ]
        assert all(receipt["status"] == "queued" for receipt in pending)
        until = time.monotonic() + 1
        while True:
            draining = control.inspect_run(instance, run, state_home=home)
            if any(
                row["request_id"] == pause["request_id"] and row["status"] == "applied"
                for row in draining["controls"]
            ):
                break
            assert time.monotonic() < until
            time.sleep(0.01)
        version = draining["state_version"]
        assert control.validate_revision(instance, state_home=home)["valid"]
        assert (
            control.inspect_run(instance, run, state_home=home)["state_version"]
            == version
        )
        if cancel:
            assert (
                control.cancel_run(instance, run, state_home=home, request_id=new_id())[
                    "status"
                ]
                == "queued"
            )
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == 0, stderr
        assert (
            json.loads(stdout)["request_id"]
            == transmitted["request_envelope"]["request_id"]
        )
        after = control.inspect_run(instance, run, state_home=home)
        assert after["status"] == ("cancelled" if cancel else "paused")
        assert (
            len(after["completions"]) == 1
            and after["completions"][0]["effective_writer"] == "stopped"
        )
        assert after["transitions"] == []
        receipts = {row["request_id"]: row for row in after["controls"]}
        for receipt in pending[:2]:
            assert receipts[receipt["request_id"]]["code"] == "shutting_down"
        inst = control.inspect_instance(instance, state_home=home)
        config_receipt = next(
            row
            for row in inst["controls"]
            if row["request_id"] == pending[2]["request_id"]
        )
        assert config_receipt["code"] == "shutting_down"
        if not cancel:
            control.resume_run(
                instance,
                run,
                state_home=home,
                request_id=new_id(),
                expected_state_version=after["state_version"],
            )
            final = control.inspect_run(instance, run, state_home=home)
            assert final["status"] == "succeeded" and final["count"] == 1
            assert not (project / ".millrace-router-owner/owner.json").exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()


def test_cancel_uncertainty_terminalizes_only_after_writer_confirmation(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(b"", b"", "execution_failure", "unknown"),
    )
    before = start_run(instance, state_home=home)
    run = str(before["id"])
    cancelled = control.cancel_run(instance, run, state_home=home, request_id=new_id())
    assert cancelled["action"] == "cancel_requested"
    unresolved = control.inspect_run(instance, run, state_home=home)
    assert (
        unresolved["status"] == "paused"
        and (project / ".millrace-router-owner/owner.json").exists()
    )
    control.reconcile_invocation(
        instance,
        run,
        invocation_id=before["invocations"][0]["id"],
        reason="verified stopped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=unresolved["state_version"],
        plane_id="main",
    )
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "cancelled"
    assert final["count"] == 1 and final["transitions"] == []
    assert not (project / ".millrace-router-owner/owner.json").exists()


def test_pause_without_target_requires_explicit_redirect(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "decision"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = str(before["id"])
    assert before["planes"]["main"]["cursor"] is None
    refused = control.resume_plane(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
        plane_id="main",
    )
    assert (
        refused["code"] == "redirect_required"
        and refused["state_version"] == before["state_version"]
    )
    redirected = control.redirect_plane(
        instance,
        run,
        node_id="draft",
        reason="continue explicitly",
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
        plane_id="main",
    )
    assert redirected["status"] == "applied"
    after = control.inspect_run(instance, run, state_home=home)
    assert (
        after["count"] == 2
        and after["status"] == "paused"
        and after["retry_counts"]["main"] == {}
    )
    control.cancel_run(instance, run, state_home=home, request_id=new_id())
    assert control.inspect_run(instance, run, state_home=home)["status"] == "cancelled"


@pytest.mark.parametrize(
    "corruption", ["body", "version", "target", "fingerprint", "final_regression"]
)
def test_corrupt_request_links_stop_ordered_drain(
    single_flow: tuple[Path, Path, Path], corruption: str
) -> None:
    import sqlite3

    from millrace.router.control_requests import append, envelope
    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = str(before["id"])
    config = control.load_instance(instance, home)
    store = Store(config)
    request = envelope(
        "resume_run",
        instance,
        run,
        new_id(),
        before["state_version"],
        {},
        plane_id=None,
    )
    append(store, request)
    later = envelope("cancel_run", instance, run, new_id(), None, {}, plane_id=None)
    append(store, later)
    if corruption == "final_regression":
        store.close()
        # Resolve the first request, then regress a durable final status only.
        control.pause_run(instance, run, state_home=home, request_id=new_id())
        connection = sqlite3.connect(config.store_path)
        connection.execute(
            "UPDATE controls SET status='queued' WHERE request_id=?",
            (request["request_id"],),
        )
    else:
        store.close()
        connection = sqlite3.connect(config.store_path)
        if corruption == "version":
            connection.execute(
                "UPDATE controls SET expected_version=999 WHERE request_id=?",
                (request["request_id"],),
            )
        elif corruption == "target":
            connection.execute(
                "UPDATE controls SET run_id=? WHERE request_id=?",
                (new_id(), request["request_id"]),
            )
        elif corruption == "body":
            body = {**request, "unknown": True}
            connection.execute(
                "UPDATE controls SET body=? WHERE request_id=?",
                (command_graph_json(body), request["request_id"]),
            )
        else:
            receipt = json.loads(
                connection.execute(
                    "SELECT receipt FROM controls WHERE request_id=?",
                    (request["request_id"],),
                ).fetchone()[0]
            )
            receipt["request_digest"] = "0" * 64
            connection.execute(
                "UPDATE controls SET receipt=? WHERE request_id=?",
                (command_graph_json(receipt), request["request_id"]),
            )
    connection.commit()
    original = list(connection.iterdump())
    connection.close()
    with pytest.raises(RouterError, match="control_integrity"):
        control.pause_run(instance, run, state_home=home, request_id=new_id())
    connection = sqlite3.connect(config.store_path)
    assert list(connection.iterdump()) == original
    connection.close()


@pytest.mark.parametrize("original_cancelled", [False, True])
def test_cancel_intent_requires_applied_control_authority(
    single_flow: tuple[Path, Path, Path], original_cancelled: bool
) -> None:
    import sqlite3

    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run_id = str(before["id"])
    if original_cancelled:
        control.cancel_run(instance, run_id, state_home=home, request_id=new_id())
    config = control.load_instance(instance, home)
    connection = sqlite3.connect(config.store_path)
    run = json.loads(
        connection.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()[0]
    )
    assert run["cancel_requested"] is original_cancelled
    run["cancel_requested"] = not original_cancelled
    connection.execute(
        "UPDATE runs SET body=? WHERE id=?", (command_graph_json(run), run_id)
    )
    connection.commit()
    corrupted = list(connection.iterdump())
    connection.close()
    with pytest.raises(RouterError, match="run_integrity"):
        control.inspect_run(instance, run_id, state_home=home)
    connection = sqlite3.connect(config.store_path)
    assert list(connection.iterdump()) == corrupted
    connection.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "writer_version",
        "state_request",
        "state_missing",
        "state_noop",
        "state_cancel_noop",
    ],
)
def test_control_fact_requires_exact_applying_receipt(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    import sqlite3

    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY"}',
            b"",
            "completed",
            "may_live" if corruption == "writer_version" else "stopped",
            exit_code=0,
        ),
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run_id = str(before["id"])
    if corruption == "writer_version":
        applied = control.reconcile_invocation(
            instance,
            run_id,
            invocation_id=before["invocations"][0]["id"],
            reason="verified stopped",
            stopped=True,
            state_home=home,
            request_id=new_id(),
            expected_state_version=before["state_version"],
            plane_id="main",
        )
    elif corruption in {"state_noop", "state_cancel_noop"}:
        applied = control.cancel_run(
            instance, run_id, state_home=home, request_id=new_id()
        )
    else:
        paused = control.pause_run(
            instance, run_id, state_home=home, request_id=new_id()
        )
        assert paused["status"] == "applied"
        before = control.inspect_run(instance, run_id, state_home=home)
        applied = control.resume_run(
            instance,
            run_id,
            state_home=home,
            request_id=new_id(),
            expected_state_version=before["state_version"],
        )
    after = control.inspect_run(instance, run_id, state_home=home)
    assert control.inspect_run(instance, run_id, state_home=home) == after
    if corruption == "state_cancel_noop":
        recovered = control.cancel_run(
            instance, run_id, state_home=home, request_id=new_id()
        )
        assert recovered["action"] == "already_terminal"
    else:
        recovered = control.recover_run(
            instance,
            run_id,
            state_home=home,
            request_id=new_id(),
            expected_state_version=after["state_version"],
        )
    if corruption in {"state_noop", "state_cancel_noop"}:
        assert recovered["state_version"] == applied["state_version"]
    connection = sqlite3.connect(control.load_instance(instance, home).store_path)
    if corruption == "writer_version":
        connection.execute("UPDATE writer_facts SET version=1")
    else:
        gate_row = connection.execute(
            "SELECT rowid,body FROM run_gate_events WHERE run_id=? AND version=?",
            (run_id, applied["state_version"]),
        ).fetchone()
        assert gate_row is not None
        forged = json.loads(gate_row[1])
        forged["request_id"] = (
            None if corruption == "state_missing" else recovered["request_id"]
        )
        connection.execute(
            "UPDATE run_gate_events SET body=? WHERE rowid=?",
            (json.dumps(forged), gate_row[0]),
        )
    connection.commit()
    corrupted = list(connection.iterdump())
    connection.close()
    with pytest.raises(RouterError, match="control_integrity|writer_integrity"):
        control.inspect_run(instance, run_id, state_home=home)
    connection = sqlite3.connect(control.load_instance(instance, home).store_path)
    assert list(connection.iterdump()) == corrupted
    connection.close()


@pytest.mark.parametrize("table", ["candidates", "activations"])
def test_missing_revision_control_link_refuses(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    table: str,
) -> None:
    import sqlite3

    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        ),
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run_id = str(before["id"])
    stopped = control.pause_run(instance, run_id, state_home=home, request_id=new_id())
    assert stopped["status"] == "applied"
    before = control.inspect_run(instance, run_id, state_home=home)
    graph["limits"]["max_invocations"] = 5
    workflow.write_text(command_graph_json(graph))
    applied = control.submit_revision(
        instance,
        run_id,
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
    )
    if table == "activations":
        pending = control.inspect_run(instance, run_id, state_home=home)
        applied = control.accept_revision(
            instance,
            run_id,
            candidate_id=pending["pending_revision"]["candidate_id"],
            state_home=home,
            request_id=new_id(),
            expected_state_version=pending["state_version"],
        )
    assert applied["status"] == "applied", applied
    after = control.inspect_run(instance, run_id, state_home=home)
    assert control.inspect_run(instance, run_id, state_home=home) == after
    connection = sqlite3.connect(control.load_instance(instance, home).store_path)
    connection.execute(
        f"UPDATE {table} SET request_id=NULL WHERE request_id=?",
        (applied["request_id"],),
    )
    connection.commit()
    corrupted = list(connection.iterdump())
    connection.close()
    with pytest.raises(RouterError, match="control_integrity|revision_integrity"):
        control.inspect_run(instance, run_id, state_home=home)
    connection = sqlite3.connect(control.load_instance(instance, home).store_path)
    assert list(connection.iterdump()) == corrupted
    connection.close()


def test_pause_state_cannot_select_later_same_version_noop(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3
    import threading

    from millrace.router.controller import Controller
    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}
    request_id = new_id()
    later_id = new_id()
    first_applied, later_applied = threading.Event(), threading.Event()
    original_apply = Controller.apply

    def apply(owner, request):
        result = original_apply(owner, request)
        if request["request_id"] == request_id:
            first_applied.set()
        if request["request_id"] == later_id:
            later_applied.set()
        return result

    monkeypatch.setattr(Controller, "apply", apply)

    def invoke(*args: object) -> CommandResult:
        queued = control.pause_run(
            instance, str(ids["run_id"]), state_home=home, request_id=request_id
        )
        assert queued["status"] == "queued"
        assert first_applied.wait(timeout=3)  # owner commits before next queued stop
        assert (
            control.pause_run(
                instance, str(ids["run_id"]), state_home=home, request_id=later_id
            )["status"]
            == "queued"
        )
        assert later_applied.wait(timeout=3)  # no completion commit before no-op
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    before = start_run(instance, state_home=home, on_started=ids.update)
    run_id = str(before["id"])
    assert before["pause_reason"] == "operator_pause"
    applied = control.pause_run(
        instance, run_id, state_home=home, request_id=request_id
    )
    later = control.pause_run(instance, run_id, state_home=home, request_id=later_id)
    assert later["state_version"] == applied["state_version"]
    after = control.inspect_run(instance, run_id, state_home=home)
    assert after["count"] == 1 and after["state_version"] == before["state_version"]
    assert (
        control.pause_run(instance, run_id, state_home=home, request_id=request_id)
        == applied
    )
    connection = sqlite3.connect(control.load_instance(instance, home).store_path)
    connection.execute(
        "UPDATE states SET request_id=? WHERE request_id=?",
        (later["request_id"], request_id),
    )
    connection.commit()
    corrupted = list(connection.iterdump())
    connection.close()
    with pytest.raises(RouterError, match="control_integrity"):
        control.inspect_run(instance, run_id, state_home=home)
    connection = sqlite3.connect(control.load_instance(instance, home).store_path)
    assert list(connection.iterdump()) == corrupted
    connection.close()


def test_exact_queued_retry_without_holder_becomes_controller(
    single_flow: tuple[Path, Path, Path],
) -> None:
    from millrace.router.control_requests import append, envelope

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    request = envelope(
        "resume_plane",
        instance,
        str(before["id"]),
        new_id(),
        before["state_version"],
        {},
        plane_id="main",
    )
    store = Store(control.load_instance(instance, home))
    queued = append(store, request)
    store.close()
    assert queued["status"] == "queued"
    final = control.execute_request(request, state_home=home)
    assert final["status"] == "applied"
    after = control.inspect_run(instance, str(before["id"]), state_home=home)
    assert after["count"] == 2 and after["status"] == "paused"
    assert control.execute_request(request, state_home=home) == final
    assert control.inspect_run(instance, str(before["id"]), state_home=home) == after


def test_closing_busy_receipt_stays_final_after_holder_crash(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router.controller import Controller

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = str(before["id"])
    config = control.load_instance(instance, home)
    actual_poll = Controller.poll
    busy: dict[str, object] = {}
    old_holder: str | None = None

    class Crash(BaseException):
        pass

    def closing(controller: Controller, active: bool) -> bool:
        nonlocal old_holder
        queue = json.loads((config.state_dir / ".queue.json").read_text())
        if queue["state"] == "closing":
            old_holder = queue["holder_id"]
            busy.update(
                control.pause_run(instance, run, state_home=home, request_id=new_id())
            )
            assert (
                busy["status"] == "refused"
                and busy["code"] == "busy"
                and busy["retryable"] is True
            )
            raise Crash()
        return actual_poll(controller, active)

    with monkeypatch.context() as patch:
        patch.setattr(Controller, "poll", closing)
        with pytest.raises(Crash):
            control.submit_revision(
                instance,
                run,
                state_home=home,
                request_id=new_id(),
                expected_state_version=before["state_version"],
            )
    current = control.inspect_run(instance, run, state_home=home)

    def invocation(*args: object) -> CommandResult:
        queue = json.loads((config.state_dir / ".queue.json").read_text())
        assert queue["state"] == "open" and queue["holder_id"] != old_holder
        assert (
            control.pause_run(instance, run, state_home=home, request_id=new_id())[
                "status"
            ]
            == "queued"
        )
        args[4]()
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invocation)
    control.resume_plane(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=current["state_version"],
        plane_id="main",
    )
    after = control.inspect_run(instance, run, state_home=home)
    assert after["count"] == 2 and after["status"] == "paused"
    assert (
        control.pause_run(instance, run, state_home=home, request_id=busy["request_id"])
        == busy
    )
    assert control.inspect_run(instance, run, state_home=home) == after


@pytest.mark.parametrize(
    "reason",
    ["operator_pause", "locked_edit", "writer_unknown", "cancellation_unresolved"],
)
def test_deleted_pause_authority_refuses_after_continuation(
    single_flow, monkeypatch, reason
):
    import sqlite3

    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids = {}
    calls = 0

    def invoke(*args):
        nonlocal calls
        calls += 1
        if calls == 1 and reason == "operator_pause":
            control.pause_run(
                instance, ids["run_id"], state_home=home, request_id=new_id()
            )
            args[4]()
        if calls == 1 and reason == "locked_edit":
            graph = json.loads(workflow.read_text())
            graph["limits"]["max_invocations"] = 5
            workflow.write_text(command_graph_json(graph))
        return CommandResult(
            b'{"outcome":"READY"}',
            b"",
            "completed",
            "may_live"
            if calls == 1 and reason in {"writer_unknown", "cancellation_unresolved"}
            else "stopped",
            exit_code=0,
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    paused = start_run(instance, state_home=home, on_started=ids.update)
    run = str(paused["id"])
    if reason == "cancellation_unresolved":
        control.cancel_run(instance, run, state_home=home, request_id=new_id())
    if reason in {"writer_unknown", "cancellation_unresolved"}:
        current = control.inspect_run(instance, run, state_home=home)
        control.reconcile_invocation(
            instance,
            run,
            invocation_id=current["invocations"][0]["id"],
            reason="stopped",
            stopped=True,
            state_home=home,
            request_id=new_id(),
            expected_state_version=current["state_version"],
            plane_id="main",
        )
    current = control.inspect_run(instance, run, state_home=home)
    if current["status"] != "cancelled":
        if reason == "locked_edit":
            control.accept_revision(
                instance,
                run,
                candidate_id=current["pending_revision"]["candidate_id"],
                state_home=home,
                request_id=new_id(),
                expected_state_version=current["state_version"],
            )
        else:
            control.resume_run(
                instance,
                run,
                state_home=home,
                request_id=new_id(),
                expected_state_version=current["state_version"],
            )
    final = control.inspect_run(instance, run, state_home=home)
    assert control.inspect_run(instance, run, state_home=home) == final
    path = control.load_instance(instance, home).store_path
    with sqlite3.connect(path) as db:
        recorded_reason = (
            "writer_unknown" if reason == "cancellation_unresolved" else reason
        )
        assert (
            db.execute("DELETE FROM states WHERE reason=?", (recorded_reason,)).rowcount
            >= 1
        )
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
    with pytest.raises(RouterError):
        control.inspect_run(instance, run, state_home=home)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before


@pytest.mark.parametrize("kind", ["redirect", "retry"])
def test_move_must_resolve_exact_retained_completion(single_flow, monkeypatch, kind):
    import sqlite3

    from millrace.router.control_requests import append, envelope
    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids = {}

    def invoke(*args):
        control.pause_run(instance, ids["run_id"], state_home=home, request_id=new_id())
        args[4]()
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    paused = start_run(instance, state_home=home, on_started=ids.update)
    run = str(paused["id"])
    local = control.pause_plane(
        instance, run, plane_id="main", state_home=home, request_id=new_id()
    )
    assert local["status"] == "applied"
    paused = control.inspect_run(instance, run, state_home=home)
    store = Store(control.load_instance(instance, home))
    request = envelope(
        "redirect_plane" if kind == "redirect" else "retry_invocation",
        instance,
        run,
        new_id(),
        paused["state_version"],
        {"node_id": "draft", "reason": "explicit redirect"}
        if kind == "redirect"
        else {
            "invocation_id": paused["invocations"][0]["id"],
            "reason": "explicit retry",
        },
        plane_id="main",
    )
    append(store, request)
    store.close()
    control.pause_run(instance, run, state_home=home, request_id=new_id())
    after = control.inspect_run(instance, run, state_home=home)
    assert after["operator_moves"][0]["completion_id"] is not None
    assert control.inspect_run(instance, run, state_home=home) == after
    assert control.execute_request(request, state_home=home)["status"] == "applied"
    control.cancel_run(instance, run, state_home=home, request_id=new_id())
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "cancelled" and final["count"] == 1
    assert final == control.inspect_run(instance, run, state_home=home)
    path = control.load_instance(instance, home).store_path
    with sqlite3.connect(path) as db:
        db.execute("UPDATE moves SET completion_id=NULL")
    with sqlite3.connect(path) as db:
        corrupt = list(db.iterdump())
    for _ in range(2):
        with pytest.raises(RouterError, match="transition_integrity"):
            control.inspect_run(instance, run, state_home=home)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == corrupt


@pytest.mark.parametrize(
    "op,closing", [("recover_run", False), ("recover_run", True), ("pause_run", True)]
)
def test_two_process_busy_has_no_transient_queued_row(
    single_flow, monkeypatch, op, closing
):
    import subprocess
    import sys

    from millrace.router import controller

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    config = control.load_instance(instance, home)
    (config.state_dir / ".queue.json").write_text(
        command_graph_json(
            {"instance_id": instance, "state": "closing" if closing else "open"}
        )
    )
    code = (
        "from pathlib import Path\n"
        "from millrace.router.ownership import file_lock\nimport sys\n"
        "with file_lock(Path(sys.argv[1])):\n"
        " print('locked',flush=True);sys.stdin.readline()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(config.state_dir / ".controller.lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    observed = []
    original = controller.finish

    def finishing(store, request, **kwargs):
        other = Store(config, readonly=True)
        try:
            observed.append(
                other.db.execute(
                    "SELECT status FROM controls WHERE request_id=?",
                    (request["request_id"],),
                ).fetchone()
            )
        finally:
            other.close()
        return original(store, request, **kwargs)

    monkeypatch.setattr(controller, "finish", finishing)
    try:
        assert child.stdout.readline().strip() == "locked"
        receipt = getattr(control, op)(
            instance,
            before["id"],
            state_home=home,
            request_id=new_id(),
            **(
                {"expected_state_version": before["state_version"]}
                if op == "recover_run"
                else {}
            ),
        )
        assert receipt["status"] == "refused"
        assert observed == [None]
        assert (
            control.inspect_run(instance, before["id"], state_home=home)["count"] == 1
        )
    finally:
        child.communicate("release\n", timeout=5)
        assert child.returncode == 0
    assert (
        control.inspect_run(instance, before["id"], state_home=home)["status"]
        == "succeeded"
    )


def test_late_pause_noop_does_not_latch_queued_resume(single_flow, monkeypatch):
    from millrace.router.control_requests import append, envelope

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        ),
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = before["id"]
    gate = control.pause_run(instance, run, state_home=home, request_id=new_id())
    assert gate["status"] == "applied"
    ready = control.resume_plane(
        instance,
        run,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
        expected_state_version=gate["state_version"],
    )
    assert ready["status"] == "applied"
    before = control.inspect_run(instance, run, state_home=home)
    store = Store(control.load_instance(instance, home))
    pause = envelope("pause_run", instance, run, new_id(), None, {}, plane_id=None)
    resume = envelope(
        "resume_run",
        instance,
        run,
        new_id(),
        before["state_version"],
        {},
        plane_id=None,
    )
    append(store, pause)
    append(store, resume)
    store.close()
    assert control.execute_request(resume, state_home=home)["status"] == "applied"
    after = control.inspect_run(instance, run, state_home=home)
    assert after["count"] == 2 and after["planes"]["main"]["pause_reason"] == "review"
    assert control.execute_request(pause, state_home=home)["action"] == "already_paused"
    assert control.inspect_run(instance, run, state_home=home) == after


def test_invocation_membership_precedes_drain_and_version(single_flow):
    from millrace.router.control_requests import append, envelope
    from millrace.router.controller import Controller
    from millrace.router.engine import Signals

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    store = Store(control.load_instance(instance, home))
    controller = Controller(store, Signals())
    controller.draining = True
    request = envelope(
        "retry_invocation",
        instance,
        before["id"],
        new_id(),
        1,
        {"invocation_id": new_id(), "reason": "wrong run"},
        plane_id="main",
    )
    append(store, request)
    try:
        assert controller.apply(request)["code"] == "invalid_target"
    finally:
        store.close()
    assert control.inspect_run(instance, before["id"], state_home=home)["count"] == 1


@pytest.mark.parametrize("timing", ["before_closing", "after_closing"])
def test_two_process_closing_order_resolves_stop(single_flow, monkeypatch, timing):
    import subprocess

    from millrace.router.controller import Controller

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = before["id"]
    config = control.load_instance(instance, home)
    original = Controller.poll
    observed = []

    def poll(holder, active):
        answer = original(holder, active)
        queue = json.loads((config.state_dir / ".queue.json").read_text())
        selected = "open" if timing == "before_closing" else "closing"
        if not observed and not active and queue["state"] == selected:
            cli = sys.executable
            child = subprocess.run(
                [
                    cli,
                    "-m",
                    "millrace.router.cli",
                    "--state-home",
                    str(home),
                    "pause",
                    "--instance",
                    instance,
                    "--run",
                    run,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert child.returncode == (0 if timing == "before_closing" else 2), (
                child.stderr
            )
            rows = [json.loads(line) for line in child.stdout.splitlines()]
            observed.extend(rows)
            assert rows[-1]["status"] == (
                "queued" if timing == "before_closing" else "refused"
            )
        return answer

    monkeypatch.setattr(Controller, "poll", poll)
    control.submit_revision(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
    )
    assert observed
    final = control.inspect_run(instance, run, state_home=home)
    assert final["count"] == 1 and all(
        row["status"] != "queued" for row in final["controls"]
    )
    replay = control.execute_request(observed[0]["request_envelope"], state_home=home)
    assert (
        replay["action"] == "paused"
        if timing == "before_closing"
        else replay["code"] == "busy"
    )
    assert control.inspect_run(instance, run, state_home=home) == final


@pytest.mark.parametrize(
    "corruption", ["deleted_state", "missing_field", "malformed_field", "wrong_target"]
)
def test_completed_uncertain_cancel_requires_application_context(
    single_flow, monkeypatch, corruption
):
    import sqlite3

    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids = {}

    def invoke(*args):
        args[5](12345)  # deterministic adapter fact; never signalled
        control.pause_run(instance, ids["run_id"], state_home=home, request_id=new_id())
        args[4]()
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "may_live", exit_code=0
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    before = start_run(instance, state_home=home, on_started=ids.update)
    run = before["id"]
    cancel = control.cancel_run(instance, run, state_home=home, request_id=new_id())
    assert cancel["active_invocations"] == [] and cancel["target_planes"] == ["main"]
    unresolved = control.inspect_run(instance, run, state_home=home)
    assert (
        unresolved["pause_reason"] == "writer_unknown"
        and unresolved["cancel_requested"]
    )
    control.reconcile_invocation(
        instance,
        run,
        invocation_id=before["invocations"][0]["id"],
        reason="stopped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=unresolved["state_version"],
        plane_id="main",
    )
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "cancelled"
    assert control.inspect_run(instance, run, state_home=home) == final
    path = control.load_instance(instance, home).store_path
    with sqlite3.connect(path) as db:
        if corruption == "deleted_state":
            assert (
                db.execute("DELETE FROM states WHERE reason='writer_unknown'").rowcount
                == 1
            )
        else:
            changed = dict(cancel)
            if corruption == "missing_field":
                del changed["active_invocations"]
            else:
                changed["active_invocations"] = (
                    new_id() if corruption == "malformed_field" else [new_id()]
                )
            encoded = command_graph_json(changed)
            db.execute(
                "UPDATE controls SET receipt=? WHERE request_id=?",
                (encoded, cancel["request_id"]),
            )
            db.execute(
                "UPDATE final_receipts SET receipt=? WHERE request_id=?",
                (encoded, cancel["request_id"]),
            )
    with sqlite3.connect(path) as db:
        corrupted = list(db.iterdump())
    with pytest.raises(RouterError, match="control_integrity"):
        control.inspect_run(instance, run, state_home=home)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == corrupted


@pytest.mark.parametrize("pause_first", [False, True])
def test_active_cancel_receipt_selects_unresolved_invocation(
    single_flow, monkeypatch, pause_first
):
    import sqlite3

    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["next"] = json.loads(
        command_graph_json(graph["planes"]["main"]["nodes"]["draft"])
    )
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {"goto": "next"}
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids, cancellation = {}, {}
    calls = 0

    def invoke(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return CommandResult(
                b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
            )
        args[5](12345)
        if pause_first:
            control.pause_run(
                instance, ids["run_id"], state_home=home, request_id=new_id()
            )
            args[4]()
        request_id = new_id()
        control.cancel_run(
            instance, ids["run_id"], state_home=home, request_id=request_id
        )
        _wait_for_cancel(args[4])
        cancellation.update(
            control.cancel_run(
                instance, ids["run_id"], state_home=home, request_id=request_id
            )
        )
        return CommandResult(b"", b"", "cancelled", "stopped")

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    final = start_run(instance, state_home=home, on_started=ids.update)
    run = final["id"]
    assert final["status"] == "cancelled" and final["count"] == 2
    assert cancellation["active_invocations"] == [final["invocations"][1]["id"]]
    assert control.inspect_run(instance, run, state_home=home)["status"] == "cancelled"
    assert (
        control.cancel_run(
            instance, run, state_home=home, request_id=cancellation["request_id"]
        )
        == cancellation
    )
    changed = {**cancellation, "active_invocations": [final["invocations"][0]["id"]]}
    path = control.load_instance(instance, home).store_path
    with sqlite3.connect(path) as db:
        encoded = command_graph_json(changed)
        db.execute(
            "UPDATE controls SET receipt=? WHERE request_id=?",
            (encoded, cancellation["request_id"]),
        )
        db.execute(
            "UPDATE final_receipts SET receipt=? WHERE request_id=?",
            (encoded, cancellation["request_id"]),
        )
    with pytest.raises(RouterError, match="control_integrity"):
        control.inspect_run(instance, run, state_home=home)


@pytest.mark.parametrize("variant", ["paused", "direct", "adapter_uncertain"])
def test_active_cancel_cleanup_reloads_and_reconciles(
    single_flow, monkeypatch, variant
):
    import sqlite3

    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids, cancellation = {}, {}

    def invoke(*args):
        args[5](12345)  # adapter acknowledgement only; never signalled
        if variant == "paused":
            control.pause_run(
                instance, ids["run_id"], state_home=home, request_id=new_id()
            )
            args[4]()
        request = new_id()
        control.cancel_run(instance, ids["run_id"], state_home=home, request_id=request)
        assert _wait_for_cancel(args[4])
        cancellation.update(
            control.cancel_run(
                instance, ids["run_id"], state_home=home, request_id=request
            )
        )
        if variant == "adapter_uncertain":
            raise RuntimeError("adapter response unavailable after cancellation")
        return CommandResult(b"", b"", "cancelled", "may_live", "cleanup_unconfirmed")

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
        on_started=ids.update,
    )
    run = ids["run_id"]
    paused = control.inspect_run(instance, run, state_home=home)
    invocation = paused["invocations"][0]
    assert cancellation["active_invocations"] == [invocation["id"]]
    assert (paused["status"], paused["pause_reason"]) == (
        "paused",
        "writer_unknown",
    )
    assert paused["count"] == 1 and paused["transitions"] == []
    assert control.inspect_run(instance, run, state_home=home) == paused
    assert control.inspect_run(instance, run, state_home=home) == paused
    path = control.load_instance(instance, home).store_path
    with sqlite3.connect(path) as db:
        raw = db.execute("SELECT * FROM completions").fetchall()
        assert len(raw) == (0 if variant == "adapter_uncertain" else 1)
        assert db.execute("SELECT COUNT(*) FROM writer_facts").fetchone()[0] == 0
    owner = project / ".millrace-router-owner/owner.json"
    assert owner.exists()
    kwargs = dict(
        invocation_id=invocation["id"],
        reason="verified stopped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
    )
    reconciled = control.reconcile_invocation(instance, run, **kwargs, plane_id="main")
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "cancelled" and final["count"] == 1
    assert final["transitions"] == [] and not owner.exists()
    assert control.inspect_run(instance, run, state_home=home) == final
    assert control.inspect_run(instance, run, state_home=home) == final
    assert (
        control.reconcile_invocation(instance, run, **kwargs, plane_id="main")
        == reconciled
    )
    assert (
        control.cancel_run(
            instance, run, state_home=home, request_id=cancellation["request_id"]
        )
        == cancellation
    )
    with sqlite3.connect(path) as db:
        if raw:
            assert db.execute("SELECT * FROM completions").fetchall() == raw
        else:
            assert (
                db.execute("SELECT transport FROM completions").fetchone()[0]
                == "interrupted"
            )
        # A null application fact cannot authorize this later automatic state.
        changed = {**cancellation, "active_invocations": []}
        encoded = command_graph_json(changed)
        for table in ("controls", "final_receipts"):
            db.execute(
                f"UPDATE {table} SET receipt=? WHERE request_id=?",
                (encoded, cancellation["request_id"]),
            )
    with sqlite3.connect(path) as db:
        corrupt = list(db.iterdump())
    with pytest.raises(RouterError, match="control_integrity"):
        control.inspect_run(instance, run, state_home=home)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == corrupt
        # A later control cannot authorize an earlier automatic aftermath.
        changed = {**cancellation, "state_version": final["state_version"]}
        for table in ("controls", "final_receipts"):
            db.execute(
                f"UPDATE {table} SET receipt=? WHERE request_id=?",
                (command_graph_json(changed), cancellation["request_id"]),
            )
    with sqlite3.connect(path) as db:
        corrupt = list(db.iterdump())
    with pytest.raises(RouterError, match="control_integrity"):
        control.inspect_run(instance, run, state_home=home)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == corrupt


def test_reconciliation_requires_writer_fact_and_no_owned_worker_at_application(
    single_flow, monkeypatch
):
    import sqlite3

    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids = {}

    def invoke(*args):
        args[5](12345)
        control.cancel_run(
            instance, ids["run_id"], state_home=home, request_id=new_id()
        )
        assert _wait_for_cancel(args[4])
        return CommandResult(b"", b"", "cancelled", "may_live", "cleanup_unconfirmed")

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        on_started=ids.update,
    )
    run = ids["run_id"]
    paused = control.inspect_run(instance, run, state_home=home)
    assert (paused["status"], paused["pause_reason"]) == (
        "paused",
        "writer_unknown",
    )
    path = control.load_instance(instance, home).store_path
    with sqlite3.connect(path) as db:
        raw = db.execute("SELECT * FROM completions").fetchall()
    kwargs = dict(
        invocation_id=paused["invocations"][0]["id"],
        reason="verified stopped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
    )
    receipt = control.reconcile_invocation(instance, run, **kwargs, plane_id="main")
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "cancelled" and final["count"] == 1
    assert final["transitions"] == []
    assert final == control.inspect_run(instance, run, state_home=home)
    assert (
        control.reconcile_invocation(instance, run, **kwargs, plane_id="main")
        == receipt
    )
    owner = project / ".millrace-router-owner/owner.json"
    assert not owner.exists()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM completions").fetchall() == raw
        assert (
            db.execute(
                "DELETE FROM states WHERE run_id=? "
                "AND reason='writer_unknown' AND request_id IS NULL",
                (run,),
            ).rowcount
            == 1
        )
    with sqlite3.connect(path) as db:
        corrupted = list(db.iterdump())
    for _ in range(2):
        with pytest.raises(RouterError, match="control_integrity"):
            control.inspect_run(instance, run, state_home=home)
    with pytest.raises(RouterError, match="control_integrity"):
        control.inspect_instance(instance, state_home=home)
    with pytest.raises(RouterError, match="control_integrity"):
        control.cancel_run(instance, run, state_home=home, request_id=new_id())
    with pytest.raises(RouterError, match="control_integrity"):
        control.recover_run(
            instance,
            run,
            state_home=home,
            request_id=new_id(),
            expected_state_version=final["state_version"],
        )
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == corrupted
    assert not owner.exists()


@pytest.mark.parametrize("corruption", ["no_cancel_authority", "wrong_prior_context"])
def test_automatic_cancel_aftermath_requires_causal_authority(
    single_flow, monkeypatch, corruption
):
    import sqlite3

    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids, cancellation = {}, {}

    def invoke(*args):
        args[5](12345)
        request = new_id()
        control.cancel_run(instance, ids["run_id"], state_home=home, request_id=request)
        assert _wait_for_cancel(args[4])
        cancellation.update(
            control.cancel_run(
                instance, ids["run_id"], state_home=home, request_id=request
            )
        )
        raise RuntimeError("adapter response unavailable after cancellation")

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        on_started=ids.update,
    )
    run = ids["run_id"]
    legal = control.inspect_run(instance, run, state_home=home)
    assert legal["pause_reason"] == "writer_unknown" and legal["count"] == 1
    assert legal == control.inspect_run(instance, run, state_home=home)
    owner = project / ".millrace-router-owner/owner.json"
    held = owner.read_bytes()
    path = control.load_instance(instance, home).store_path
    with sqlite3.connect(path) as db:
        if corruption == "no_cancel_authority":
            # Isolate the causal receipt rule from the independent current-flag check.
            for table in ("final_receipts", "controls"):
                db.execute(
                    f"DELETE FROM {table} WHERE request_id=?",
                    (cancellation["request_id"],),
                )
            body = json.loads(
                db.execute("SELECT body FROM runs WHERE id=?", (run,)).fetchone()[0]
            )
            body["cancel_requested"] = False
            db.execute(
                "UPDATE runs SET body=? WHERE id=?", (command_graph_json(body), run)
            )
        else:
            assert (
                db.execute(
                    "UPDATE states SET reason='review' WHERE run_id=? "
                    "AND reason='writer_unknown'",
                    (run,),
                ).rowcount
                == 1
            )
    with sqlite3.connect(path) as db:
        corrupted = list(db.iterdump())
    for _ in range(2):
        with pytest.raises(RouterError, match="control_integrity"):
            control.inspect_run(instance, run, state_home=home)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == corrupted
    assert owner.read_bytes() == held


@pytest.mark.parametrize("signal_count", [1, 2])
def test_signal_enters_fifo_before_active_drain(single_flow, monkeypatch, signal_count):
    import threading

    from millrace.router.controller import Controller
    from millrace.router.engine import Signals

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids, observed, signals = {}, [], []
    original_enter, original_apply = Signals.__enter__, Controller.apply
    earlier = {}
    stops_applied = threading.Event()

    def entered(owner):
        signals.append(owner)
        return original_enter(owner)

    def applied(holder, request):
        if request["request_id"] == earlier.get("request_id"):
            observed.extend(
                row[0]
                for row in holder.store.db.execute(
                    "SELECT operation FROM controls WHERE status='queued' ORDER BY seq"
                )
            )
        result = original_apply(holder, request)
        if request["operation"] == ("cancel_run" if signal_count == 2 else "pause_run"):
            stops_applied.set()
        return result

    def invoke(*args):
        args[5](12345)
        run = ids["run_id"]
        earlier.update(
            control.resume_run(
                instance,
                run,
                state_home=home,
                request_id=new_id(),
                expected_state_version=control.inspect_run(
                    instance, run, state_home=home
                )["state_version"],
            )
        )
        assert earlier["status"] == "queued"
        signals[0].count = signal_count
        assert stops_applied.wait(timeout=3)
        cancelled = args[4]()
        assert observed == ["resume_run", "pause_run"] + (
            ["cancel_run"] if signal_count == 2 else []
        )
        return CommandResult(
            b'{"outcome":"READY"}',
            b"",
            "cancelled" if cancelled else "completed",
            "stopped",
            exit_code=0,
        )

    monkeypatch.setattr(Signals, "__enter__", entered)
    monkeypatch.setattr(Controller, "apply", applied)
    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        on_started=ids.update,
    )
    run = ids["run_id"]
    view = control.inspect_run(instance, run, state_home=home)
    assert view["count"] == 1 and view["transitions"] == []
    assert view["status"] == ("cancelled" if signal_count == 2 else "paused")
    assert [row["operation"] for row in view["controls"]] == observed
    assert all(row["status"] != "queued" for row in view["controls"])
    assert view == control.inspect_run(instance, run, state_home=home)
    if signal_count == 1:
        control.cancel_run(instance, run, state_home=home, request_id=new_id())
    assert not (project / ".millrace-router-owner/owner.json").exists()


@pytest.mark.parametrize("signal_count", [1, 2])
def test_prestart_signal_survives_frontier(single_flow, monkeypatch, signal_count):
    from millrace.router.engine import Signals

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    entered = Signals.__enter__

    def signalled(owner):
        entered(owner)
        owner.count = signal_count
        return owner

    monkeypatch.setattr(Signals, "__enter__", signalled)
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: pytest.fail("prestart stop dispatched work"),
    )
    receipt = control.start_run(
        instance, state_home=home, request_id=new_id(), expected_state_version=1
    )
    view = control.inspect_run(instance, receipt["run_id"], state_home=home)
    assert view["count"] == 0 and view["invocations"] == []
    assert view["status"] == ("paused" if signal_count == 1 else "cancelled")
    assert [row["operation"] for row in view["controls"]] == ["pause_run"] + (
        ["cancel_run"] if signal_count == 2 else []
    )
    assert all(row["status"] == "applied" for row in view["controls"])
    assert view == control.inspect_run(instance, receipt["run_id"], state_home=home)
    if signal_count == 1:
        control.cancel_run(
            instance, receipt["run_id"], state_home=home, request_id=new_id()
        )
    assert not (project / ".millrace-router-owner/owner.json").exists()


def test_active_poll_uses_finite_entry_frontier(single_flow, monkeypatch):
    import threading

    from millrace.router.controller import Controller

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids, first, deferred, holders = {}, {"request_id": new_id()}, [], []
    snapshots = []
    observed_frontiers = threading.Event()
    original_poll = Controller.poll
    applied = Controller.apply

    def applying(owner, request):
        result = applied(owner, request)
        if request["request_id"] == first.get("request_id"):
            deferred.append(
                control.pause_run(
                    instance, ids["run_id"], state_home=home, request_id=new_id()
                )
            )
        elif deferred and request["request_id"] == deferred[0]["request_id"]:
            deferred.append(
                control.resume_run(
                    instance,
                    ids["run_id"],
                    state_home=home,
                    request_id=new_id(),
                    expected_state_version=owner.store.run(ids["run_id"])[
                        "state_version"
                    ],
                )
            )
        return result

    def poll(owner, active):
        result = original_poll(owner, active)
        if active and len(deferred) > len(snapshots):
            view = owner.store.inspection(ids["run_id"])
            assert view["controls"][-1]["status"] == "queued"
            snapshots.append((len(deferred), view["status"], view["pause_reason"]))
            holders.append(owner.holder_id)
            if len(snapshots) == 2:
                observed_frontiers.set()
        return result

    def invoke(*args):
        args[5](12345)
        run = ids["run_id"]
        first.update(
            control.resume_run(
                instance,
                run,
                state_home=home,
                request_id=first["request_id"],
                expected_state_version=control.inspect_run(
                    instance, run, state_home=home
                )["state_version"],
            )
        )
        assert observed_frontiers.wait(timeout=3)
        assert snapshots == [(1, "running", None), (2, "paused", "operator_pause")]
        assert holders[0] == holders[1]
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr(Controller, "poll", poll)
    monkeypatch.setattr(Controller, "apply", applying)
    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        on_started=ids.update,
    )
    run = ids["run_id"]
    paused = control.inspect_run(instance, run, state_home=home)
    assert paused["count"] == 1 and paused["transitions"] == []
    assert [row["status"] for row in paused["controls"]] == [
        "refused",
        "applied",
        "refused",
    ]
    assert paused["controls"][-1]["code"] == "shutting_down"
    assert paused == control.inspect_run(instance, run, state_home=home)
    control.resume_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
    )
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "succeeded" and final["count"] == 1
    assert final == control.inspect_run(instance, run, state_home=home)
    assert not (project / ".millrace-router-owner/owner.json").exists()


@pytest.mark.parametrize("phase", ["corruption", "closing"])
def test_frontier_preserves_corruption_and_closing(single_flow, monkeypatch, phase):
    import sqlite3

    from millrace.router.control_requests import append, envelope
    from millrace.router.controller import Controller
    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids, selected, queued = {}, {}, []
    original_apply, original_queue = Controller.apply, Controller.queue_state

    def applying(owner, request):
        result = original_apply(owner, request)
        if phase == "corruption" and request["request_id"] == selected.get(
            "request_id"
        ):
            request = envelope(
                "pause_run", instance, ids["run_id"], new_id(), None, {}, plane_id=None
            )
            append(owner.store, request)
            owner.store.db.execute(
                "UPDATE controls SET expected_version=99 WHERE request_id=?",
                (request["request_id"],),
            )
            queued.append(request["request_id"])
        return result

    def closing(owner, state):
        original_queue(owner, state)
        if phase == "closing" and state == "closing" and not queued:
            request = envelope(
                "pause_run", instance, ids["run_id"], new_id(), None, {}, plane_id=None
            )
            append(owner.store, request)
            queued.append(request["request_id"])

    def invoke(*args):
        args[5](12345)
        run = ids["run_id"]
        if phase == "corruption":
            selected.update(
                control.resume_run(
                    instance,
                    run,
                    state_home=home,
                    request_id=new_id(),
                    expected_state_version=control.inspect_run(
                        instance, run, state_home=home
                    )["state_version"],
                )
            )
        else:
            control.pause_run(instance, run, state_home=home, request_id=new_id())
        assert args[4]() is False
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr(Controller, "apply", applying)
    monkeypatch.setattr(Controller, "queue_state", closing)
    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    if phase == "corruption":
        with pytest.raises(RouterError, match="control_integrity"):
            control.start_run(
                instance,
                state_home=home,
                request_id=new_id(),
                expected_state_version=1,
                on_started=ids.update,
            )
        path = control.load_instance(instance, home).store_path
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT stdout,writer FROM completions").fetchall() == [
                (b'{"outcome":"READY"}', "stopped")
            ]
            assert (
                db.execute(
                    "SELECT status FROM controls WHERE request_id=?", (queued[0],)
                ).fetchone()[0]
                == "queued"
            )
            before = list(db.iterdump())
        with pytest.raises(RouterError, match="control_integrity"):
            control.inspect_run(instance, ids["run_id"], state_home=home)
        with sqlite3.connect(path) as db:
            assert list(db.iterdump()) == before
    else:
        control.start_run(
            instance,
            state_home=home,
            request_id=new_id(),
            expected_state_version=1,
            on_started=ids.update,
        )
        view = control.inspect_run(instance, ids["run_id"], state_home=home)
        assert queued and all(row["status"] != "queued" for row in view["controls"])
        assert view["count"] == 1 and view["pause_reason"] == "operator_pause"
        control.cancel_run(
            instance, ids["run_id"], state_home=home, request_id=new_id()
        )
        final = control.inspect_run(instance, ids["run_id"], state_home=home)
        assert final["status"] == "cancelled" and final == control.inspect_run(
            instance, ids["run_id"], state_home=home
        )


def test_signal_during_predispatch_drain_prevents_launch(single_flow, monkeypatch):
    from millrace.router import controller, engine
    from millrace.router.engine import Signals

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids, signals, dispatched = {}, [], []
    claimed = fired = False
    enter, claim, validate = (
        Signals.__enter__,
        engine.claim_hold,
        controller.validate_requests,
    )

    def entered(owner):
        signals.append(owner)
        return enter(owner)

    def claiming(*args, **kwargs):
        nonlocal claimed
        claim(*args, **kwargs)
        claimed = True

    def validating(store):
        nonlocal fired
        validate(store)
        if claimed and not fired:
            fired = True
            signals[0].count = 1

    def invoke(*args):
        dispatched.append(True)
        args[5](12345)
        args[4]()
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr(Signals, "__enter__", entered)
    monkeypatch.setattr(engine, "claim_hold", claiming)
    monkeypatch.setattr(controller, "validate_requests", validating)
    monkeypatch.setattr(engine, "invoke", invoke)
    control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        on_started=ids.update,
    )
    run = ids["run_id"]
    view = control.inspect_run(instance, run, state_home=home)
    try:
        assert fired and not dispatched
        assert view["status"] == "paused" and view["pause_reason"] == "operator_pause"
        assert (
            view["count"] == 0
            and view["invocations"] == []
            and view["completions"] == []
        )
        assert view == control.inspect_run(instance, run, state_home=home)
        (pause,) = view["controls"]
        assert pause["operation"] == "pause_run" and pause["action"] == "paused"
        assert (
            control.pause_run(
                instance, run, state_home=home, request_id=pause["request_id"]
            )
            == pause
        )
        assert view == control.inspect_run(instance, run, state_home=home)
    finally:
        control.cancel_run(instance, run, state_home=home, request_id=new_id())
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "cancelled" and final["count"] == 0
    assert final == control.inspect_run(instance, run, state_home=home)
    assert not (project / ".millrace-router-owner/owner.json").exists()


@pytest.mark.parametrize("arrival", ["empty_validation", "request_application"])
def test_second_signal_during_closing_drain_is_applied(
    single_flow, monkeypatch, arrival
):
    from millrace.router import controller, engine
    from millrace.router.control_requests import append, envelope
    from millrace.router.controller import Controller
    from millrace.router.engine import Signals

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids, signals, resume = {}, [], {}
    closing = fired = False
    enter, queue, apply = Signals.__enter__, Controller.queue_state, Controller.apply
    validate = controller.validate_requests

    def entered(owner):
        signals.append(owner)
        return enter(owner)

    def queued(owner, state):
        nonlocal closing
        queue(owner, state)
        closing = closing or state == "closing"
        if state == "closing" and arrival == "request_application" and not resume:
            run = owner.current()
            resume.update(
                envelope(
                    "resume_run",
                    instance,
                    run["id"],
                    new_id(),
                    run["state_version"],
                    {},
                    plane_id=None,
                )
            )
            append(owner.store, resume)

    def validating(store):
        nonlocal fired
        validate(store)
        if closing and arrival == "empty_validation" and not fired:
            fired = True
            signals[0].count = 2

    def applying(owner, request):
        nonlocal fired
        result = apply(owner, request)
        if request["request_id"] == resume.get("request_id"):
            fired = True
            signals[0].count = 2
        return result

    def invoke(*args):
        args[5](12345)
        signals[0].count = 1
        assert args[4]() is False
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr(Signals, "__enter__", entered)
    monkeypatch.setattr(Controller, "queue_state", queued)
    monkeypatch.setattr(controller, "validate_requests", validating)
    monkeypatch.setattr(Controller, "apply", applying)
    monkeypatch.setattr(engine, "invoke", invoke)
    control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        on_started=ids.update,
    )
    run = ids["run_id"]
    final = control.inspect_run(instance, run, state_home=home)
    try:
        assert fired and final["status"] == "cancelled"
        assert final["count"] == 1 and final["transitions"] == []
        assert final["completions"][0]["writer"] == "stopped"
        assert final == control.inspect_run(instance, run, state_home=home)
        assert all(row["status"] != "queued" for row in final["controls"])
        cancel = final["controls"][-1]
        assert cancel["operation"] == "cancel_run" and cancel["action"] == "cancelled"
        assert (
            control.cancel_run(
                instance, run, state_home=home, request_id=cancel["request_id"]
            )
            == cancel
        )
        if resume:
            refused = next(
                row
                for row in final["controls"]
                if row["request_id"] == resume["request_id"]
            )
            assert refused["code"] == "shutting_down"
            assert (
                control.resume_run(
                    instance,
                    run,
                    state_home=home,
                    request_id=resume["request_id"],
                    expected_state_version=resume["expected_state_version"],
                )
                == refused
            )
        assert final == control.inspect_run(instance, run, state_home=home)
    finally:
        control.cancel_run(instance, run, state_home=home, request_id=new_id())
    assert not (project / ".millrace-router-owner/owner.json").exists()


def test_closing_repeat_readmission_requires_loop(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second stop arriving inside re-admission must reach cancellation."""
    from collections.abc import Callable
    from typing import cast

    from millrace.router import controller, engine
    from millrace.router.controller import Controller
    from millrace.router.engine import Signals

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    holders: list[Signals] = []
    closing = False
    injected = calls = 0
    enter, queue = Signals.__enter__, Controller.queue_state
    validate = controller.validate_requests

    def entered(owner: Signals) -> Signals:
        holders.append(owner)
        return enter(owner)

    def queued(owner: Controller, state: str) -> None:
        nonlocal closing
        queue(owner, state)
        closing = closing or state == "closing"

    def validating(store: Store) -> None:
        nonlocal injected
        validate(store)
        if closing and injected < 2:
            injected += 1
            # First stop during closing; second during the re-admission drain.
            holders[0].count = injected

    def invoke(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        deadline: float,
        cancelled: Callable[[], bool],
        on_started: Callable[[int], None],
    ) -> CommandResult:
        nonlocal calls
        calls += 1
        on_started(4242)
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr(Signals, "__enter__", entered)
    monkeypatch.setattr(Controller, "queue_state", queued)
    monkeypatch.setattr(controller, "validate_requests", validating)
    monkeypatch.setattr(engine, "invoke", invoke)
    receipt = control.start_run(
        instance, state_home=home, request_id=new_id(), expected_state_version=1
    )
    run = str(receipt["run_id"])
    final = control.inspect_run(instance, run, state_home=home)
    try:
        assert injected == 2 and calls == 1
        assert final["status"] == "cancelled" and final["count"] == 1
        completion = cast(list[dict[str, object]], final["completions"])[0]
        assert completion["effective_writer"] == "stopped"
        assert completion["stdout"] == '{"outcome":"READY"}'
        assert final == control.inspect_run(instance, run, state_home=home)
        rows = cast(list[dict[str, object]], final["controls"])
        assert [(row["operation"], row.get("action")) for row in rows] == [
            ("pause_run", "paused"),
            ("cancel_run", "cancelled"),
        ]
        for row in rows:
            replay = (
                control.pause_run
                if row["operation"] == "pause_run"
                else control.cancel_run
            )
            assert (
                replay(
                    instance, run, state_home=home, request_id=str(row["request_id"])
                )
                == row
            )
    finally:
        control.cancel_run(instance, run, state_home=home, request_id=new_id())
    assert not (project / ".millrace-router-owner/owner.json").exists()


def test_validate_run_context_uses_same_read_snapshot(flow, monkeypatch):
    from tests.router.test_planes import initial_run

    import millrace.router.control as public

    store, run_id = initial_run(flow)
    instance = store.config.instance_id
    store.close()
    opened = []
    observed = []
    original_predicate = public.check_run_compatibility
    original_store = public.Store

    class ReadonlySnapshot(original_store):
        def __init__(self, *args, **kwargs):
            assert kwargs["readonly"] is True
            super().__init__(*args, **kwargs)
            assert self.db.in_transaction
            self.connection_identity = id(self.db)
            self.transaction_statements = []
            self.db.set_trace_callback(self.transaction_statements.append)
            self.was_closed = False
            opened.append(self)

        def close(self):
            self.was_closed = True
            super().close()

    def inspect_snapshot(startup, candidate, planes, unrouted):
        assert len(opened) == 1
        snapshot = opened[0]
        assert not snapshot.was_closed and snapshot.db.in_transaction
        assert id(snapshot.db) == snapshot.connection_identity
        assert not any(
            sql.strip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK"))
            for sql in snapshot.transaction_statements
        )
        assert planes == snapshot.plane_states(run_id)
        observed.append(snapshot.run(run_id)["state_version"])
        return original_predicate(startup, candidate, planes, unrouted)

    monkeypatch.setattr(public, "Store", ReadonlySnapshot)
    monkeypatch.setattr(public, "check_run_compatibility", inspect_snapshot)
    result = public.validate_revision(instance, state_home=flow[2], run_id=run_id)
    assert result["valid"] is True, result
    assert result["validation_context"]["run_state_version"] == observed[0] == 1
    assert set(result["resolved_nodes"]) == {"main"}
    assert len(opened) == 1 and opened[0].was_closed
    assert observed == [1]
    # Historical identity loading must not follow a changed live project symlink.
    project = flow[1]
    relocated = project.with_name("retained-original-project")
    project.rename(relocated)
    replacement = project.with_name("different-current-project")
    replacement.mkdir()
    project.symlink_to(replacement, target_is_directory=True)
    config = public.load_instance(instance, flow[2])
    cold = original_store(config, readonly=True)
    try:
        assert cold.plane_states(run_id)["main"]["cursor"] == "draft"
    finally:
        cold.close()


def test_advisory_workspaces_follow_authored_graph_not_creation_or_run(
    flow, monkeypatch
):
    import copy

    from millrace.router.control_requests import append, envelope, finish
    from millrace.router.controller import Controller
    from millrace.router.engine import Signals

    workflow, project, home = flow
    made = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    instance = made["instance_id"]
    authored = json.loads(workflow.read_text())
    (project / "current-run").mkdir()
    authored["planes"]["main"]["workspace"] = "current-run"
    authored["planes"]["aux"] = copy.deepcopy(authored["planes"]["main"])
    workflow.write_text(command_graph_json(authored))
    config = control.load_instance(instance, home)
    store = Store(config)
    # Admit an initial run at the controller fixture boundary; no engine dispatch.
    request = envelope(
        "start_run", instance, None, new_id(), 1, {"edit_mode": "locked"}, plane_id=None
    )
    append(store, request)
    with store.transaction():
        receipt = Controller(store, Signals()).effect(request, None)
        finish(store, request, result=receipt)
    run_id = receipt["run_id"]
    run_revision = store.run(run_id)["revision_id"]
    first_revision = store.db.execute(
        "SELECT id FROM revisions ORDER BY rowid LIMIT 1"
    ).fetchone()[0]
    assert first_revision != run_revision
    store.close()
    original_stat = Path.stat

    def no_live_workspace_probe(path, *args, **kwargs):
        assert not path.is_relative_to(project), path
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", no_live_workspace_probe)
    captures = []
    original_capture = control.capture_candidate

    def observe_capture(*args, **kwargs):
        candidate = original_capture(*args, **kwargs)
        captures.append((kwargs["baseline"], candidate))
        return candidate

    monkeypatch.setattr(control, "capture_candidate", observe_capture)
    unchanged = control.validate_revision(instance, state_home=home, run_id=run_id)
    assert unchanged["valid"] is True, unchanged
    assert unchanged["validation_context"]["run_state_version"] == 1
    assert captures[-1][0].revision_id == run_revision
    before = config.store_path.read_bytes()
    drafts = []
    added = copy.deepcopy(authored)
    added["planes"]["future"] = copy.deepcopy(added["planes"]["main"])
    added["planes"]["future"]["workspace"] = "not-created/future"
    drafts.append(added)
    removed = copy.deepcopy(authored)
    del removed["planes"]["aux"]
    drafts.append(removed)
    changed = copy.deepcopy(authored)
    changed["planes"]["main"]["workspace"] = "not-created/main"
    drafts.append(changed)
    for draft in drafts:
        workflow.write_text(command_graph_json(draft))
        result = control.validate_revision(instance, state_home=home)
        assert result["valid"] is True, result
        assert result["validation_context"]["kind"] == "instance"
        assert result["validation_context"]["run_id"] is None
        assert "run_state_version" not in result["validation_context"]
        assert set(result["resolved_nodes"]) == set(draft["planes"])
        baseline, candidate = captures[-1]
        assert baseline is None
        for plane_id, plane in draft["planes"].items():
            assert candidate.revision.workspaces[plane_id] == {
                "path": str(project / plane["workspace"]),
                "device": 0,
                "inode": 0,
            }
        targeted = control.validate_revision(instance, state_home=home, run_id=run_id)
        assert targeted["valid"] is False, targeted
        assert config.store_path.read_bytes() == before
