"""Strict P9 contracts; runtime concurrency qualification belongs to P9b."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from millrace.router.ownership import new_id
from millrace.router.records import RouterError


def authored() -> dict:
    node = {
        "invoke": {"binding": "python", "config": {"args": ["-c", "print('{}')"]}},
        "result": "json-v1",
        "outcomes": ["READY"],
        "on": {"READY": {"finish": "succeeded"}},
        "on_error": {"pause": {"reason": "error", "resume": "step"}},
        "on_unknown": {"pause": {"reason": "unknown", "resume": "step"}},
    }
    return {
        "format": "millrace-router/v3",
        "limits": {
            "max_invocations": 8,
            "max_elapsed_seconds": 40,
            "max_parallel_invocations": 2,
        },
        "bindings": {
            "python": {
                "adapter": "command",
                "contract": "millrace-command/v1",
                "executable": "python3",
                "config": {},
            }
        },
        "planes": {
            "alpha": {
                "start": "step",
                "workspace": "alpha",
                "nodes": {"step": copy.deepcopy(node)},
            },
            "beta": {
                "start": "step",
                "workspace": "beta",
                "nodes": {"step": copy.deepcopy(node)},
            },
        },
        "seams": {
            "ready": {
                "producer": {"plane": "alpha", "node": "step", "outcome": "READY"},
                "consumer": {"plane": "beta", "node": "step"},
            }
        },
    }


def test_v3_authored_topology_and_captured_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router.graph import capture, decode_revision, parse_graph

    data = authored()
    graph = parse_graph(json.dumps(data).encode())
    assert set(graph.planes) == {"alpha", "beta"}
    assert graph.max_parallel == 2 and graph.planes["alpha"].workspace == "alpha"
    for mutation in ("old", "cross", "cycle", "consumer", "parallel"):
        bad = copy.deepcopy(data)
        if mutation == "old":
            bad["format"] = "millrace-router/v2"
        if mutation == "cross":
            bad["planes"]["beta"]["nodes"]["beta_only"] = copy.deepcopy(
                bad["planes"]["beta"]["nodes"]["step"]
            )
            bad["planes"]["beta"]["nodes"]["step"]["on"]["READY"] = {
                "goto": "beta_only"
            }
            bad["planes"]["alpha"]["nodes"]["step"]["on"]["READY"] = {
                "goto": "beta_only"
            }
        if mutation == "cycle":
            for current, other in (("alpha", "beta"), ("beta", "alpha")):
                bad["planes"][current]["nodes"]["step"]["requires_terminal"] = [
                    {"plane": other, "status": "succeeded"}
                ]
        if mutation == "consumer":
            bad["seams"]["again"] = copy.deepcopy(bad["seams"]["ready"])
        if mutation == "parallel":
            bad["limits"]["max_parallel_invocations"] = 3
        with pytest.raises(RouterError):
            parse_graph(json.dumps(bad).encode())
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    for name in ("alpha", "beta"):
        (project / name).mkdir()
    path = workflow / "workflow.json"
    path.write_text(json.dumps(data))
    revision = capture(path, project)
    # Retained reload checks captured links, not current directories.
    encoded = revision.encoded()
    (project / "alpha").rmdir()
    (project / "beta").rmdir()
    with monkeypatch.context() as patch:
        patch.setattr(
            Path, "resolve", lambda *a, **k: pytest.fail("historical resolve")
        )
        patch.setattr(Path, "is_dir", lambda *a, **k: pytest.fail("historical is_dir"))
        assert decode_revision(encoded, revision.revision_id) == revision
    (project / "alpha").mkdir()
    (project / "beta").symlink_to(project / "alpha", target_is_directory=True)
    with pytest.raises(RouterError):
        capture(path, project)


def test_run_compatibility_checks_startup_and_all_retained_links(
    tmp_path: Path,
) -> None:
    from millrace.router.graph import capture, revision
    from millrace.router.planes import check_run_compatibility

    data = authored()
    data["seams"] = {}
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    for name in data["planes"]:
        (project / name).mkdir()
    path = workflow / "workflow.json"
    path.write_text(json.dumps(data))
    startup = capture(path, project)
    states = {
        name: {"cursor": "step", "resume_target": "step"} for name in data["planes"]
    }
    retained = (
        {
            "plane_id": "alpha",
            "node_id": "step",
            "classification": "semantic",
            "outcome": "READY",
        },
    )

    def candidate(value: dict):
        return revision(
            {"workflow.json": json.dumps(value).encode()},
            startup.executables,
            workspaces=startup.workspaces,
        )

    expanded = copy.deepcopy(data)
    expanded["planes"]["alpha"]["nodes"]["later"] = copy.deepcopy(
        expanded["planes"]["alpha"]["nodes"]["step"]
    )
    expanded["planes"]["alpha"]["nodes"]["step"]["on"]["READY"] = {"goto": "later"}
    check_run_compatibility(startup, candidate(expanded), states, retained)
    for mutation in ("workspace", "seam", "outcome", "resume", "dependency"):
        bad = copy.deepcopy(expanded)
        targets = copy.deepcopy(states)
        if mutation == "workspace":
            bad["planes"]["beta"]["workspace"] = "different"
        if mutation == "seam":
            bad["seams"] = authored()["seams"]
        if mutation == "outcome":
            bad["planes"]["alpha"]["nodes"]["step"]["outcomes"] = ["OTHER"]
            bad["planes"]["alpha"]["nodes"]["step"]["on"] = {"OTHER": {"goto": "later"}}
        if mutation == "resume":
            targets["beta"]["resume_target"] = "missing"
        if mutation == "dependency":
            bad["planes"]["alpha"]["nodes"]["later"]["requires_terminal"] = [
                {"plane": "beta", "status": "failed"}
            ]
        with pytest.raises(RouterError):
            check_run_compatibility(startup, candidate(bad), targets, retained)

    targets = copy.deepcopy(states)
    targets["alpha"].update(cursor="later", resume_target="later")
    with pytest.raises(RouterError, match="alpha.cursor"):
        check_run_compatibility(startup, candidate(data), targets, retained)
    replacement = copy.deepcopy(data)
    node = replacement["planes"]["alpha"]["nodes"].pop("step")
    node["on_error"]["pause"]["resume"] = "replacement"
    node["on_unknown"]["pause"]["resume"] = "replacement"
    replacement["planes"]["alpha"].update(
        start="replacement", nodes={"replacement": node}
    )
    targets["alpha"].update(cursor="replacement", resume_target="replacement")
    with pytest.raises(RouterError, match="retained producing node"):
        check_run_compatibility(startup, candidate(replacement), targets, retained)


def initial_run(flow: tuple[Path, Path, Path]):
    from millrace.router import control
    from millrace.router.control_requests import append, envelope, finish
    from millrace.router.controller import Controller
    from millrace.router.engine import Signals
    from millrace.router.store import Store, new_id

    workflow, project, home = flow
    made = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    config = control.load_instance(made["instance_id"], home)
    store = Store(config)
    request = envelope(
        "start_run",
        config.instance_id,
        None,
        new_id(),
        1,
        {"edit_mode": "locked"},
        plane_id=None,
    )
    append(store, request)
    with store.transaction():
        result = Controller(store, Signals()).effect(request, None)
        finish(store, request, result=result)
    return store, result["run_id"]


def test_v7_initial_plane_projection_replay_and_corruption(
    flow: tuple[Path, Path, Path],
) -> None:
    from millrace.router.store import FORMAT, Store

    assert FORMAT == "millrace-router-store/v7"
    store, run_id = initial_run(flow)
    assert set(store.plane_states(run_id)) == {"main"}
    assert store.plane_states(run_id)["main"]["cursor"] == "draft"
    assert store.run(run_id)["run_gate"] is None
    captured = store.revision(str(store.run(run_id)["revision_id"]))
    assert "check" in captured.graph.planes["main"].nodes
    config = store.config
    store.close()
    reopened = Store(config, readonly=True)
    assert reopened.plane_states(run_id)["main"]["status"] == "ready"
    reopened.close()
    with sqlite3.connect(config.store_path) as db:
        row = db.execute("SELECT body FROM planes WHERE run_id=?", (run_id,)).fetchone()
        value = json.loads(row[0])
        assert "check" != value["cursor"]
        value["cursor"] = "check"
        db.execute(
            "UPDATE planes SET body=? WHERE run_id=?", (json.dumps(value), run_id)
        )
        before = list(db.iterdump())
    with pytest.raises(RouterError):
        Store(config)
    with sqlite3.connect(config.store_path) as db:
        assert list(db.iterdump()) == before


def test_request_v2_exact_scope_and_start_identity() -> None:
    from millrace.router.control_requests import (
        OPERATIONS,
        STOPS,
        envelope,
        validate_envelope,
    )

    assert OPERATIONS == {
        "start_run",
        "recover_run",
        "pause_run",
        "cancel_run",
        "resume_run",
        "submit_revision",
        "accept_revision",
        "retry_invocation",
        "reconcile_invocation",
        "update_instance_config",
        "pause_plane",
        "resume_plane",
        "cancel_plane",
        "redirect_plane",
    }
    assert STOPS == {"pause_run", "cancel_run", "pause_plane", "cancel_plane"}
    common = ("1" * 32, "2" * 32, "3" * 32)
    request = envelope("pause_plane", *common, None, {}, plane_id="alpha")
    assert request["format"] == "millrace-router-request/v2"
    assert set(request) == {
        "format",
        "operation",
        "instance_id",
        "run_id",
        "plane_id",
        "request_id",
        "expected_state_version",
        "payload",
    }
    start = envelope(
        "start_run",
        common[0],
        None,
        common[2],
        1,
        {"edit_mode": "locked"},
        plane_id=None,
    )
    assert start["run_id"] is None and start["plane_id"] is None
    for values in (
        {"plane_id": None},
        {"expected_state_version": 1},
        {"format": "millrace-router-request/v1"},
        {"operation": "redirect_run"},
    ):
        with pytest.raises(RouterError):
            validate_envelope({**request, **values})
    for op in ("resume_run", "recover_run"):
        with pytest.raises(RouterError):
            envelope(op, *common, 1, {}, plane_id="alpha")
    for values in ({"extra": 1}, {"payload": {"extra": 1}}):
        with pytest.raises(RouterError):
            validate_envelope({**request, **values})
    for values in ({"run_id": common[1]}, {"plane_id": "alpha"}):
        with pytest.raises(RouterError):
            validate_envelope({**start, **values})
    with pytest.raises(RouterError):
        envelope(
            "retry_invocation",
            *common,
            1,
            {"invocation_id": "4" * 32, "reason": "test"},
            plane_id=None,
        )
    released = envelope(
        "release_project_hold",
        *common,
        None,
        {
            "project_root": "/project",
            "hold_id": "4" * 32,
            "reason": "stopped",
            "stopped": True,
        },
        plane_id=None,
    )
    assert released["plane_id"] is None


def test_scoped_controls_global_gates_and_replay(flow):
    from millrace.router import control
    from millrace.router.control_requests import envelope

    store, run_id = initial_run(flow)
    instance = store.config.instance_id
    store.close()
    request = envelope(
        "pause_plane", instance, run_id, new_id(), None, {}, plane_id="main"
    )
    receipt = control.execute_request(request, state_home=flow[2])
    assert receipt["status"] == "applied", receipt
    assert receipt["target_planes"] == ["main"]
    assert receipt["active_invocations"] == []
    view = control.inspect_run(instance, run_id, state_home=flow[2])
    assert view["planes"]["main"]["status"] == "paused"
    assert view["status"] == "paused" and view["run_gate"] is None
    assert control.execute_request(request, state_home=flow[2]) == receipt


def test_final_allowed_invocation_finishes_without_limit_pause(single_flow):
    from millrace.router import control

    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    data["limits"]["max_invocations"] = 1
    workflow.write_text(json.dumps(data))
    made = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    instance = made["instance_id"]
    started = control.start_run(
        instance, state_home=home, request_id=new_id(), expected_state_version=1
    )
    assert started["status"] == "applied", started
    view = control.inspect_run(instance, started["run_id"], state_home=home)
    assert view["count"] == 1 and view["status"] == "succeeded"
    assert view["run_gate"] is None
    assert view["planes"]["main"]["status"] == "succeeded"
    assert len(view["transitions"]) == 1


def test_local_targets_and_independent_cursor_aftermath(single_flow, monkeypatch):
    import os
    import threading
    from contextlib import contextmanager

    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import Store

    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    node = data["planes"].pop("main")
    data["limits"]["max_parallel_invocations"] = 2
    data["planes"] = {
        name: {**copy.deepcopy(node), "workspace": name} for name in ("alpha", "beta")
    }
    for name in data["planes"]:
        (project / name).mkdir()
    alpha_nodes = data["planes"]["alpha"]["nodes"]
    alpha_nodes["second"] = copy.deepcopy(alpha_nodes["draft"])
    alpha_nodes["draft"]["on"]["READY"] = {"goto": "second"}
    alpha_nodes["second"]["on"]["READY"] = {"finish": "failed"}
    workflow.write_text(json.dumps(data))
    arrived = threading.Barrier(2)
    calls = []
    owner = threading.get_ident()
    original_active = Store.active
    original_transaction = Store.transaction
    started_ids = []
    actual_workers = []
    committed = set()

    @contextmanager
    def owner_transaction(store):
        assert threading.get_ident() == owner
        with original_transaction(store):
            yield

    def active(store, invocation_id, pid):
        assert threading.get_ident() == owner
        original_active(store, invocation_id, pid)
        started_ids.append(invocation_id)
        committed.add(invocation_id)

    def invoke(argv, cwd, env, deadline, poll, on_started):
        assert threading.get_ident() != owner
        actual_workers.append(threading.current_thread())
        on_started(os.getpid())
        with sqlite3.connect(config.store_path.as_uri() + "?mode=ro", uri=True) as db:
            active_id, status, raw = db.execute(
                "SELECT id,status,body FROM invocations WHERE plane_id=? "
                "AND status='active'",
                (cwd.name,),
            ).fetchone()
            assert active_id in committed and status == "active"
            assert json.loads(raw)["started_version"] is not None
        calls.append(cwd.name)
        if calls.count(cwd.name) == 1:
            arrived.wait(timeout=3)
        assert poll() is False
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr(Store, "active", active)
    monkeypatch.setattr(Store, "transaction", owner_transaction)
    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    made = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    instance = made["instance_id"]
    config = control.load_instance(instance, home)
    started = control.start_run(
        instance, state_home=home, request_id=new_id(), expected_state_version=1
    )
    result = control.inspect_run(instance, started["run_id"], state_home=home)
    assert sorted(calls) == ["alpha", "alpha", "beta"]
    assert result["status"] == "failed" and result["count"] == 3
    assert result["quiescent"] and result["inflight_invocations"] == []
    assert {row["plane_id"] for row in result["invocations"]} == {"alpha", "beta"}
    assert set(result["planes"]) == {"alpha", "beta"}
    assert result["planes"]["alpha"]["status"] == "failed"
    assert result["planes"]["beta"]["status"] == "succeeded"
    for name in ("alpha", "beta"):
        assert result["planes"][name]["workspace"]["path"] == str(project / name)
    assert {(row["plane_id"], row["node"]) for row in result["transitions"]} == {
        ("alpha", "draft"),
        ("alpha", "second"),
        ("beta", "draft"),
    }
    assert set(started_ids) == {row["id"] for row in result["invocations"]}
    assert len(started_ids) == len(set(started_ids)) == 3
    assert len(actual_workers) == 3 and all(
        not thread.is_alive() for thread in actual_workers
    )


def test_probe_gap_stop_snapshot_and_exact_set_replay(single_flow, monkeypatch):
    import os
    import threading

    from millrace.router import control
    from millrace.router.control_requests import envelope
    from millrace.router.records import CommandResult

    workflow, project, home = single_flow
    made = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    instance = made["instance_id"]
    probe = threading.Event()
    release = threading.Event()
    ids = {}
    queued = []
    requests = []

    def invoke(argv, cwd, env, deadline, poll, on_started):
        probe.set()
        assert release.wait(timeout=3)
        on_started(os.getpid())
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    def operator():
        assert probe.wait(timeout=3)
        request = envelope(
            "pause_plane", instance, ids["run_id"], new_id(), None, {}, plane_id="main"
        )
        requests.append(request)
        queued.append(control.execute_request(request, state_home=home))
        release.set()

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    thread = threading.Thread(target=operator)
    thread.start()
    try:
        control.start_run(
            instance,
            state_home=home,
            request_id=new_id(),
            expected_state_version=1,
            on_started=ids.update,
        )
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive() and queued[0]["status"] == "queued"
    view = control.inspect_run(instance, ids["run_id"], state_home=home)
    invocation = view["invocations"][0]
    receipt = control.execute_request(requests[0], state_home=home)
    assert receipt["active_invocations"] == [invocation["id"]]
    assert receipt["target_planes"] == ["main"]
    assert invocation["started_version"] > receipt["state_version"]
    assert (
        view["quiescent"]
        and len(view["completions"]) == 1
        and view["transitions"] == []
    )
    # Exact replay is immutable even after the writer has stopped.
    assert control.execute_request(requests[0], state_home=home) == receipt
    config = control.load_instance(instance, home)
    for forged in (
        dict(receipt, active_invocations=[]),
        dict(receipt, active_invocations=[invocation["id"], invocation["id"]]),
        dict(receipt, active_invocations=sorted([invocation["id"], new_id()])),
        dict(receipt, target_planes=["invented"]),
    ):
        with sqlite3.connect(config.store_path) as db:
            db.execute(
                "UPDATE controls SET receipt=? WHERE request_id=?",
                (json.dumps(forged), receipt["request_id"]),
            )
            db.execute(
                "UPDATE final_receipts SET receipt=? WHERE request_id=?",
                (json.dumps(forged), receipt["request_id"]),
            )
            damaged = list(db.iterdump())
        with pytest.raises(RouterError, match="control_integrity"):
            control.inspect_run(instance, ids["run_id"], state_home=home)
        with sqlite3.connect(config.store_path) as db:
            assert list(db.iterdump()) == damaged


@pytest.mark.parametrize("edit_mode", ["live", "locked", "live_paused_plane"])
def test_multi_active_result_first_activation_barrier(
    single_flow, monkeypatch, edit_mode
):
    import os
    import threading

    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import Store

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    template = graph["planes"].pop("main")
    graph["limits"]["max_parallel_invocations"] = 2
    graph["planes"] = {
        name: {**copy.deepcopy(template), "workspace": name}
        for name in ("alpha", "beta")
    }
    for name in graph["planes"]:
        (project / name).mkdir()
    workflow.write_text(json.dumps(graph))
    barrier = threading.Barrier(2)
    release_beta = threading.Event()
    original_complete = Store.complete
    persisted = []
    old_revision = []

    def invoke(argv, cwd, env, deadline, poll, on_started):
        on_started(os.getpid())
        barrier.wait(timeout=3)
        if cwd.name == "beta":
            assert release_beta.wait(timeout=3)
        return CommandResult(
            json.dumps({"outcome": "READY" if cwd.name == "alpha" else "NEW"}).encode(),
            b"",
            "completed",
            "stopped",
            exit_code=0,
        )

    def completed(store, invocation_id, result, **kwargs):
        original_complete(store, invocation_id, result, **kwargs)
        row = store.db.execute(
            "SELECT * FROM invocations WHERE id=?", (invocation_id,)
        ).fetchone()
        old_revision.append(row["revision_id"])
        run = store.run(row["run_id"])
        assert run["revision_id"] == row["revision_id"]
        assert store.db.execute("SELECT count(*) FROM transitions").fetchone()[0] == 0
        persisted.append(invocation_id)
        if row["plane_id"] == "alpha":
            if edit_mode == "live_paused_plane":
                stopped = control.pause_plane(
                    store.config.instance_id,
                    row["run_id"],
                    plane_id="alpha",
                    state_home=home,
                    request_id=new_id(),
                )
                assert stopped["status"] == "queued", stopped
            assert (
                store.db.execute(
                    "SELECT stdout FROM completions WHERE invocation_id=?",
                    (invocation_id,),
                ).fetchone()[0]
                == result.stdout
            )
            graph["planes"]["alpha"]["nodes"]["draft"]["on"]["READY"] = {
                "finish": "failed"
            }
            beta = graph["planes"]["beta"]["nodes"]["draft"]
            beta["outcomes"].append("NEW")
            beta["on"]["NEW"] = {"finish": "failed"}
            beta["on_unknown"] = {"finish": "succeeded"}
            workflow.write_text(json.dumps(graph))
            release_beta.set()

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    monkeypatch.setattr(Store, "complete", completed)
    made = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    started = control.start_run(
        made["instance_id"],
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        edit_mode="live" if edit_mode == "live_paused_plane" else edit_mode,
    )
    result = control.inspect_run(
        made["instance_id"], started["run_id"], state_home=home
    )
    assert len(persisted) == 2 and len(set(old_revision)) == 1
    if edit_mode == "locked":
        assert result["pause_reason"] == "locked_edit" and result["transitions"] == []
        assert len(result["completions"]) == 2 and result["quiescent"]
        accepted = control.accept_revision(
            made["instance_id"],
            started["run_id"],
            state_home=home,
            request_id=new_id(),
            expected_state_version=result["state_version"],
            candidate_id=result["pending_revision"]["candidate_id"],
        )
        assert accepted["status"] == "applied", accepted
        result = control.inspect_run(
            made["instance_id"], started["run_id"], state_home=home
        )
    if edit_mode == "live_paused_plane":
        assert result["status"] == "paused" and result["run_gate"] is None
        assert result["planes"]["alpha"]["status"] == "paused"
        assert result["planes"]["alpha"]["pause_reason"] == "operator_pause"
        assert result["planes"]["beta"]["status"] == "succeeded"
        assert len(result["completions"]) == 2 and result["count"] == 2
        assert [row["plane_id"] for row in result["transitions"]] == ["beta"]
        assert (
            result["transitions"][0]["version"] == result["activations"][-1]["version"]
        )
        continued = control.resume_plane(
            made["instance_id"],
            started["run_id"],
            plane_id="alpha",
            state_home=home,
            request_id=new_id(),
            expected_state_version=result["state_version"],
        )
        assert continued["status"] == "applied", continued
        result = control.inspect_run(
            made["instance_id"], started["run_id"], state_home=home
        )
    assert result["status"] == "failed" and result["count"] == 2
    assert result["planes"]["alpha"]["status"] == "failed"
    assert result["planes"]["beta"]["status"] == "succeeded"
    beta_result = next(
        row for row in result["completions"] if row["plane_id"] == "beta"
    )
    assert (
        beta_result["classification"] == "unknown" and beta_result["outcome"] == "NEW"
    )
    assert len(result["activations"]) == 2 and len(result["transitions"]) == 2
    activation = result["activations"][-1]
    assert activation["version"] >= max(
        row["completed_version"] for row in result["invocations"]
    )
    assert all(
        row["revision_id"] == activation["revision_id"] != old_revision[0]
        for row in result["transitions"]
    )
    if edit_mode == "live_paused_plane":
        assert [row["plane_id"] for row in result["transitions"]] == ["beta", "alpha"]
        assert result["transitions"][1]["version"] > result["transitions"][0]["version"]
    else:
        assert [row["plane_id"] for row in result["transitions"]] == ["alpha", "beta"]
        assert len({row["version"] for row in result["transitions"]}) == 1


def test_v2_control_preconditions_gates_and_plane_state(single_flow):
    from millrace.router import control

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    base = graph["planes"].pop("main")
    graph["planes"] = {
        name: {**copy.deepcopy(base), "workspace": name} for name in ("alpha", "beta")
    }
    graph["planes"]["alpha"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    for name in graph["planes"]:
        (project / name).mkdir()
    workflow.write_text(json.dumps(graph))
    store, run_id = initial_run(single_flow)
    instance = store.config.instance_id
    store.close()
    local_stop = control.pause_plane(
        instance, run_id, plane_id="alpha", state_home=home, request_id=new_id()
    )
    assert local_stop["status"] == "applied"
    derived = control.inspect_run(instance, run_id, state_home=home)
    assert derived["count"] == 1 and derived["planes"]["beta"]["status"] == "succeeded"
    assert (
        derived["pause_reason"] == "no_runnable_planes" and derived["run_gate"] is None
    )
    refused = control.resume_run(
        instance,
        run_id,
        state_home=home,
        request_id=new_id(),
        expected_state_version=derived["state_version"],
    )
    assert (
        refused["code"] == "run_not_paused"
        and refused["state_version"] == derived["state_version"]
    )
    stop = control.pause_run(instance, run_id, state_home=home, request_id=new_id())
    assert (
        stop["action"] == "paused" and stop["state_version"] > derived["state_version"]
    )
    repeated = control.pause_run(instance, run_id, state_home=home, request_id=new_id())
    assert (
        repeated["action"] == "already_paused"
        and repeated["state_version"] == stop["state_version"]
    )
    ready = control.resume_plane(
        instance,
        run_id,
        plane_id="alpha",
        state_home=home,
        request_id=new_id(),
        expected_state_version=stop["state_version"],
    )
    assert ready["status"] == "applied"
    held = control.inspect_run(instance, run_id, state_home=home)
    assert held["count"] == 1 and held["run_gate"]["reason"] == "operator_pause"
    assert held["planes"]["alpha"]["status"] == "ready"
    continued = control.resume_run(
        instance,
        run_id,
        state_home=home,
        request_id=new_id(),
        expected_state_version=held["state_version"],
    )
    assert continued["status"] == "applied"
    after = control.inspect_run(instance, run_id, state_home=home)
    assert after["count"] == 2 and after["run_gate"] is None
    assert after["planes"]["alpha"]["pause_reason"] == "review"
    assert after["planes"]["beta"] == derived["planes"]["beta"]
    terminal = control.redirect_plane(
        instance,
        run_id,
        plane_id="beta",
        node_id="draft",
        reason="no revival",
        state_home=home,
        request_id=new_id(),
        expected_state_version=after["state_version"],
    )
    assert terminal["code"] == "plane_terminal"
    stopped = control.cancel_plane(
        instance, run_id, plane_id="alpha", state_home=home, request_id=new_id()
    )
    assert stopped["status"] == "applied"
    final = control.inspect_run(instance, run_id, state_home=home)
    assert final["status"] == "cancelled" and final["count"] == 2
    assert final["planes"]["beta"] == derived["planes"]["beta"]


@pytest.mark.parametrize("forged_status", ["failed", "cancelled"])
def test_aggregate_terminal_state_replays_from_plane_facts(single_flow, forged_status):
    import sqlite3

    from tests.router.support import create_instance, start_run

    from millrace.router import control
    from millrace.router.records import RouterError

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == result["planes"]["main"]["status"] == "succeeded"
    config = control.load_instance(instance, home)
    with sqlite3.connect(config.store_path) as db:
        body = json.loads(db.execute("SELECT body FROM runs").fetchone()[0])
        body["status"] = forged_status
        db.execute("UPDATE runs SET body=?", (json.dumps(body),))
        assert (
            db.execute(
                "UPDATE states SET status=? WHERE run_id=? AND version=?",
                (forged_status, result["id"], result["state_version"]),
            ).rowcount
            == 1
        )
    with sqlite3.connect(config.store_path) as db:
        before = list(db.iterdump())
    for _ in range(2):
        with pytest.raises(RouterError, match="run_integrity"):
            control.inspect_run(instance, result["id"], state_home=home)
    with sqlite3.connect(config.store_path) as db:
        assert list(db.iterdump()) == before


def test_global_cap_routes_all_saved_plane_results_before_refusing_dispatch(
    single_flow, monkeypatch
):
    import copy
    import threading

    from tests.router.support import create_instance, start_run

    from millrace.router import control
    from millrace.router.records import CommandResult

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    base = graph["planes"].pop("main")
    graph["planes"] = {
        name: {**copy.deepcopy(base), "workspace": name} for name in ("alpha", "beta")
    }
    for name in graph["planes"]:
        (project / name).mkdir()
    alpha = graph["planes"]["alpha"]["nodes"]
    alpha["after"] = copy.deepcopy(alpha["draft"])
    alpha["draft"]["on"]["READY"] = {"goto": "after"}
    graph["limits"].update(max_invocations=2, max_parallel_invocations=2)
    workflow.write_text(json.dumps(graph))
    barrier = threading.Barrier(2)

    def invoke(*args):
        args[5](12345)
        barrier.wait(timeout=3)
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert (
        result["count"] == len(result["completions"]) == len(result["transitions"]) == 2
    )
    assert result["planes"]["beta"]["status"] == "succeeded"
    assert result["planes"]["alpha"]["cursor"] == "after"
    assert result["planes"]["alpha"]["status"] == "ready"
    assert result["pause_reason"] == result["run_gate"]["reason"] == "limit_exhausted"
    resumed = control.resume_run(
        instance,
        result["id"],
        state_home=home,
        request_id=new_id(),
        expected_state_version=result["state_version"],
    )
    assert resumed["status"] == "refused" and resumed["code"] == "limit_exhausted"
    after = control.inspect_run(instance, result["id"], state_home=home)
    assert after["count"] == 2 and after["transitions"] == result["transitions"]
    assert (
        after["planes"] == result["planes"] and after["run_gate"] == result["run_gate"]
    )


def test_writer_promotion_reconciliation_and_edit_blocker_preserve_seam_pin(
    single_flow, monkeypatch
):
    import copy

    from tests.router.support import create_instance, start_run

    from millrace.router import control
    from millrace.router.records import CommandResult

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    base = graph["planes"].pop("main")
    graph["planes"] = {
        name: {**copy.deepcopy(base), "workspace": name}
        for name in ("producer", "consumer")
    }
    for name in graph["planes"]:
        (project / name).mkdir()
    graph["seams"] = {
        "handoff": {
            "producer": {"plane": "producer", "node": "draft", "outcome": "READY"},
            "consumer": {"plane": "consumer", "node": "draft"},
        }
    }
    workflow.write_text(json.dumps(graph))
    original_bytes = workflow.read_bytes()
    ids = {}
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])

    def invoke(*args):
        if args[1].name == "consumer":
            control.pause_run(
                instance, ids["run_id"], state_home=home, request_id=new_id()
            )
            return CommandResult(
                b'{"outcome":"READY"}', b"", "completed", "may_live", exit_code=0
            )
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    with monkeypatch.context() as patch:
        patch.setattr("millrace.router.engine.invoke", invoke)
        before = start_run(instance, state_home=home, on_started=ids.update)
    run_id = before["id"]
    consumer = next(
        row for row in before["invocations"] if row["plane_id"] == "consumer"
    )
    assert [
        json.loads(event["body"])["reason"]
        for event in before["run_gate_events"]
        if event["kind"] == "set"
    ] == ["operator_pause", "writer_unknown"]
    assert before["planes"]["consumer"]["selected"] == {"handoff": 1}
    assert before["seam_acknowledgements"] == [] and before["count"] == 2
    raw = before["completions"]
    reconciled = control.reconcile_invocation(
        instance,
        run_id,
        plane_id="consumer",
        invocation_id=consumer["id"],
        reason="fixture confirms writer stopped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
    )
    assert reconciled["status"] == "applied", reconciled
    stopped = control.inspect_run(instance, run_id, state_home=home)
    assert stopped["run_gate"] == before["run_gate"]
    assert stopped["planes"]["consumer"]["selected"] == {"handoff": 1}
    assert stopped["seam_acknowledgements"] == [] and stopped["count"] == 2
    graph["planes"]["consumer"]["nodes"]["draft"]["outcomes"] = []
    workflow.write_text(json.dumps(graph))
    submitted = control.submit_revision(
        instance,
        run_id,
        state_home=home,
        request_id=new_id(),
        expected_state_version=stopped["state_version"],
    )
    assert submitted["status"] == "applied", submitted
    invalid = control.inspect_run(instance, run_id, state_home=home)
    assert invalid["pending_revision"]["diagnostic"] is not None
    refused = control.resume_run(
        instance,
        run_id,
        state_home=home,
        request_id=new_id(),
        expected_state_version=invalid["state_version"],
    )
    assert refused["status"] == "refused" and refused["code"] == "invalid_revision"
    retained = control.inspect_run(instance, run_id, state_home=home)
    assert retained["run_gate"] == before["run_gate"]
    assert retained["seam_acknowledgements"] == [] and retained["count"] == 2
    workflow.write_bytes(original_bytes)
    restored = control.submit_revision(
        instance,
        run_id,
        state_home=home,
        request_id=new_id(),
        expected_state_version=retained["state_version"],
    )
    assert restored["status"] == "applied", restored
    pending = control.inspect_run(instance, run_id, state_home=home)
    assert pending["pending_revision"] is None
    resumed = control.resume_run(
        instance,
        run_id,
        state_home=home,
        request_id=new_id(),
        expected_state_version=pending["state_version"],
    )
    assert resumed["status"] == "applied", resumed
    final = control.inspect_run(instance, run_id, state_home=home)
    assert final["status"] == "succeeded" and final["count"] == 2
    assert final["run_gate"] is None and final["planes"]["consumer"]["selected"] == {}
    assert len(final["seam_acknowledgements"]) == 1
    for old, current in zip(raw, final["completions"], strict=True):
        assert old["stdout"] == current["stdout"] and old["writer"] == current["writer"]
    assert control.inspect_run(instance, run_id, state_home=home) == final


def test_scoped_cancel_drains_target_worker_while_sibling_continues(
    single_flow, monkeypatch
):
    import threading
    import time

    from tests.router.support import create_instance, start_run

    from millrace.router import control
    from millrace.router.records import CommandResult

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    base = graph["planes"].pop("main")
    graph["planes"] = {
        name: {**copy.deepcopy(base), "workspace": name} for name in ("alpha", "beta")
    }
    for name in graph["planes"]:
        (project / name).mkdir()
    graph["limits"]["max_parallel_invocations"] = 2
    workflow.write_text(json.dumps(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids, stop = {}, {}
    barrier = threading.Barrier(2)
    alpha_cancelled = threading.Event()
    threads = []

    def invoke(argv, cwd, env, deadline, poll, on_started):
        threads.append(threading.current_thread())
        on_started(12345)
        barrier.wait(timeout=3)
        if cwd.name == "alpha":
            stop.update(
                control.cancel_plane(
                    instance,
                    ids["run_id"],
                    plane_id="alpha",
                    state_home=home,
                    request_id=new_id(),
                )
            )
            assert stop["status"] == "queued", stop
            until = time.monotonic() + 3
            while not poll() and time.monotonic() < until:
                threading.Event().wait(0.005)
            assert poll()
            alpha_cancelled.set()
            return CommandResult(b"", b"", "cancelled", "stopped")
        assert alpha_cancelled.wait(timeout=3)
        assert poll() is False
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    started = start_run(instance, state_home=home, on_started=ids.update)
    result = control.inspect_run(instance, started["id"], state_home=home)
    assert result["status"] == "cancelled" and result["count"] == 2
    assert result["run_gate"] is None and result["cancel_requested"] is False
    assert result["planes"]["alpha"]["status"] == "cancelled"
    assert result["planes"]["beta"]["status"] == "succeeded"
    assert [row["plane_id"] for row in result["transitions"]] == ["beta"]
    alpha = next(row for row in result["invocations"] if row["plane_id"] == "alpha")
    receipt = control.cancel_plane(
        instance,
        result["id"],
        plane_id="alpha",
        state_home=home,
        request_id=stop["request_id"],
    )
    assert receipt["target_planes"] == ["alpha"]
    assert receipt["active_invocations"] == [alpha["id"]]
    assert threads and all(not thread.is_alive() for thread in threads)
    assert control.inspect_run(instance, result["id"], state_home=home) == result


def test_workspace_replacement_after_captured_start_refuses_dispatch_then_recovers(
    single_flow, monkeypatch
):
    from millrace.router import control, engine
    from millrace.router.store import Store

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["workspace"] = "owned"
    workflow.write_text(json.dumps(graph))
    (workflow.parent / "scripts/draft.py").write_text(
        "from pathlib import Path\n"
        "Path('launched.txt').write_text('real command completed\\n')\n"
        "print('{\"outcome\":\"READY\"}')\n"
    )
    workspace = project / "owned"
    original = project / "captured-owned"
    workspace.mkdir()
    created = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    assert created["status"] == "applied", created
    instance = created["instance_id"]
    launches = []
    real_invoke = engine.invoke

    def observed_invoke(*args):
        launches.append(args[1])
        return real_invoke(*args)

    monkeypatch.setattr(engine, "invoke", observed_invoke)
    captured = {}

    def replace_after_start(ids):
        before = control.inspect_run(instance, ids["run_id"], state_home=home)
        assert before["count"] == 0 and before["invocations"] == []
        assert any(
            row["operation"] == "start_run" and row["status"] == "applied"
            for row in control.inspect_instance(instance, state_home=home)["controls"]
        )
        captured.update(before)
        assert (
            before["planes"]["main"]["workspace"]["inode"]
            == workspace.stat().st_ino
        )
        workspace.rename(original)
        workspace.mkdir()
        assert workspace.stat().st_ino != original.stat().st_ino

    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        on_started=replace_after_start,
    )
    assert started["status"] == "applied", started
    run_id = started["run_id"]
    failed = control.inspect_run(instance, run_id, state_home=home)
    assert launches == [] and not (workspace / "launched.txt").exists()
    assert not (original / "launched.txt").exists()
    assert failed["count"] == len(failed["invocations"]) == 1
    assert failed["quiescent"] and failed["inflight_invocations"] == []
    invocation = failed["invocations"][0]
    assert invocation["origin"] == "core_preparation"
    completion = failed["completions"][0]
    assert completion["diagnostic"] == "workspace_identity_changed"
    assert (
        completion["transport"] == completion["classification"] == "execution_failure"
    )
    assert completion["effective_writer"] == "stopped"
    assert completion["stdout"] == completion["stderr"] == ""
    transition = failed["transitions"][0]
    assert (
        json.loads(transition["action"])
        == graph["planes"]["main"]["nodes"]["draft"]["on_error"]
    )
    assert failed["planes"]["main"]["status"] == "paused"
    assert failed["planes"]["main"]["pause_reason"] == "execution_error"
    assert (
        failed["planes"]["main"]["workspace"]
        == captured["planes"]["main"]["workspace"]
    )
    assert failed["revision_id"] == captured["revision_id"]
    # Readonly reload validates captured history while the live directory is wrong.
    config = control.load_instance(instance, home)
    store = Store(config, readonly=True)
    try:
        retained = store.revision(str(failed["revision_id"])).workspaces
        assert retained == {
            "main": {
                key: captured["planes"]["main"]["workspace"][key]
                for key in ("path", "device", "inode")
            }
        }
    finally:
        store.close()
    workspace.rmdir()
    original.rename(workspace)
    resumed = control.resume_plane(
        instance,
        run_id,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
        expected_state_version=failed["state_version"],
    )
    assert resumed["status"] == "applied", resumed
    recovered = control.inspect_run(instance, run_id, state_home=home)
    assert launches == [workspace]
    assert (workspace / "launched.txt").read_text() == "real command completed\n"
    assert recovered["status"] == recovered["planes"]["main"]["status"] == "succeeded"
    assert recovered["count"] == len(recovered["invocations"]) == 2
    assert recovered["completions"][0] == completion
    assert recovered["transitions"][0] == transition
    assert (
        recovered["planes"]["main"]["workspace"]
        == captured["planes"]["main"]["workspace"]
    )
    assert recovered["revision_id"] == captured["revision_id"]
    assert recovered["deadline"] == captured["deadline"]
    assert recovered["quiescent"] and recovered["inflight_invocations"] == []
    assert control.inspect_run(instance, run_id, state_home=home) == recovered

def test_missing_workspace_after_captured_start_refuses_dispatch_then_recovers(
    single_flow, monkeypatch
):
    from millrace.router import control, engine
    from millrace.router.store import Store

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["workspace"] = "owned"
    workflow.write_text(json.dumps(graph))
    (workflow.parent / "scripts/draft.py").write_text(
        "from pathlib import Path\n"
        "Path('launched.txt').write_text('real command completed\\n')\n"
        "print('{\"outcome\":\"READY\"}')\n"
    )
    workspace = project / "owned"
    original = project / "captured-owned"
    workspace.mkdir()
    created = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    assert created["status"] == "applied", created
    instance = created["instance_id"]
    launches = []
    real_invoke = engine.invoke

    def observed_invoke(*args):
        launches.append(args[1])
        return real_invoke(*args)

    monkeypatch.setattr(engine, "invoke", observed_invoke)
    captured = {}

    def remove_after_start(ids):
        before = control.inspect_run(instance, ids["run_id"], state_home=home)
        assert before["count"] == 0 and before["invocations"] == []
        assert any(
            row["operation"] == "start_run" and row["status"] == "applied"
            for row in control.inspect_instance(instance, state_home=home)["controls"]
        )
        captured.update(before)
        assert (
            before["planes"]["main"]["workspace"]["inode"]
            == workspace.stat().st_ino
        )
        workspace.rename(original)
        assert not workspace.exists() and original.is_dir()

    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        on_started=remove_after_start,
    )
    assert started["status"] == "applied", started
    run_id = started["run_id"]
    failed = control.inspect_run(instance, run_id, state_home=home)
    assert launches == [] and not (workspace / "launched.txt").exists()
    assert not (original / "launched.txt").exists()
    assert failed["count"] == len(failed["invocations"]) == 1
    assert failed["quiescent"] and failed["inflight_invocations"] == []
    invocation = failed["invocations"][0]
    assert invocation["origin"] == "core_preparation"
    completion = failed["completions"][0]
    assert completion["diagnostic"] == "invalid_workspace"
    assert (
        completion["transport"] == completion["classification"] == "execution_failure"
    )
    assert completion["effective_writer"] == "stopped"
    assert completion["stdout"] == completion["stderr"] == ""
    transition = failed["transitions"][0]
    assert (
        json.loads(transition["action"])
        == graph["planes"]["main"]["nodes"]["draft"]["on_error"]
    )
    assert failed["planes"]["main"]["status"] == "paused"
    assert failed["planes"]["main"]["pause_reason"] == "execution_error"
    assert (
        failed["planes"]["main"]["workspace"]
        == captured["planes"]["main"]["workspace"]
    )
    assert failed["revision_id"] == captured["revision_id"]
    # Readonly reload validates captured history while the live directory is missing.
    config = control.load_instance(instance, home)
    store = Store(config, readonly=True)
    try:
        retained = store.revision(str(failed["revision_id"])).workspaces
        assert retained == {
            "main": {
                key: captured["planes"]["main"]["workspace"][key]
                for key in ("path", "device", "inode")
            }
        }
    finally:
        store.close()
    original.rename(workspace)
    resumed = control.resume_plane(
        instance,
        run_id,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
        expected_state_version=failed["state_version"],
    )
    assert resumed["status"] == "applied", resumed
    recovered = control.inspect_run(instance, run_id, state_home=home)
    assert launches == [workspace]
    assert (workspace / "launched.txt").read_text() == "real command completed\n"
    assert recovered["status"] == recovered["planes"]["main"]["status"] == "succeeded"
    assert recovered["count"] == len(recovered["invocations"]) == 2
    assert recovered["completions"][0] == completion
    assert recovered["transitions"][0] == transition
    assert (
        recovered["planes"]["main"]["workspace"]
        == captured["planes"]["main"]["workspace"]
    )
    assert recovered["revision_id"] == captured["revision_id"]
    assert recovered["deadline"] == captured["deadline"]
    assert recovered["quiescent"] and recovered["inflight_invocations"] == []
    assert control.inspect_run(instance, run_id, state_home=home) == recovered
