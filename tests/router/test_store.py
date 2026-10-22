import json
import sqlite3
from pathlib import Path

import pytest
from tests.router.support import (
    command_graph_json,
    create_instance,
    inspect_run,
    load_instance,
    recover_run,
    start_run,
)

from millrace.router.records import RouterError
from millrace.router.store import Store


def test_completion_transition_crash_and_corruption(
    flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == "succeeded"
    assert result["count"] == 2
    run = str(result["id"])
    assert len(result["transitions"]) == 2
    assert recover_run(instance, run, state_home=home) == inspect_run(
        instance, run, state_home=home
    )


class Crash(BaseException):
    pass


@pytest.mark.parametrize("point", ["result", "transition"])
def test_commit_windows_route_once(
    flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    workflow, project, home = flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}
    import millrace.router.engine as engine

    owner = Store if point == "result" else engine
    method = "complete" if point == "result" else "_boundary"
    original = getattr(owner, method)

    def crash(store: Store, *args: object) -> object:
        result = original(store, *args)
        if (
            point == "result"
            or store.db.execute("SELECT COUNT(*) FROM transitions").fetchone()[0]
        ):
            raise Crash()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(owner, method, crash)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    first = inspect_run(instance, str(ids["run_id"]), state_home=home)
    assert first["count"] == 1
    assert len(first["completions"]) == 1
    assert len(first["transitions"]) == (0 if point == "result" else 1)
    finished = recover_run(instance, str(ids["run_id"]), state_home=home)
    assert finished["status"] == "succeeded"
    assert finished["count"] == 2
    assert len(finished["transitions"]) == 2
    assert recover_run(instance, str(ids["run_id"]), state_home=home) == finished


@pytest.mark.parametrize(
    "corruption",
    [
        "format",
        "run_id",
        "instance",
        "revision",
        "hold",
        "status",
        "count",
        "deadline",
        "version",
        "invocation",
        "inv_status",
        "node",
        "result",
        "result_class",
        "writer",
        "exit_code",
        "transition",
        "action",
        "counter",
        "snapshot",
    ],
)
def test_corrupt_durable_links_refuse_without_repair(
    flow: tuple[Path, Path, Path], corruption: str
) -> None:
    workflow, project, home = flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    config = load_instance(instance, home)
    con = sqlite3.connect(config.store_path)
    if corruption == "format":
        con.execute("UPDATE meta SET value='old' WHERE key='format'")
    elif corruption in {
        "run_id",
        "instance",
        "revision",
        "hold",
        "status",
        "count",
        "deadline",
        "version",
    }:
        body = json.loads(con.execute("SELECT body FROM runs").fetchone()[0])
        key, value = {
            "run_id": ("id", "0" * 32),
            "instance": ("instance_id", "0" * 32),
            "revision": ("revision_id", "0" * 64),
            "hold": ("hold_id", "0" * 32),
            "status": ("status", "ready"),
            "count": ("count", 0),
            "deadline": ("deadline", body["deadline"] + 1),
            "version": ("state_version", 0),
        }[corruption]
        body[key] = value
        con.execute("UPDATE runs SET body=?", (command_graph_json(body),))
    elif corruption == "invocation":
        con.execute("UPDATE invocations SET revision_id='missing'")
    elif corruption == "inv_status":
        con.execute("UPDATE invocations SET status='active'")
    elif corruption == "node":
        con.execute("UPDATE invocations SET node='absent'")
    elif corruption == "result":
        con.execute(
            "UPDATE completions SET invocation_id='missing' "
            "WHERE invocation_id=(SELECT invocation_id FROM completions LIMIT 1)"
        )
    elif corruption == "result_class":
        con.execute("UPDATE completions SET classification='unknown'")
    elif corruption == "writer":
        con.execute("UPDATE completions SET writer='running'")
    elif corruption == "exit_code":
        con.execute("UPDATE completions SET exit_code=17")
    elif corruption == "transition":
        con.execute("UPDATE transitions SET target='draft'")
    elif corruption == "action":
        con.execute("UPDATE transitions SET action='{}'")
    elif corruption == "counter":
        con.execute(
            "INSERT INTO retries VALUES(?,?,?,?)", (result["id"], "main", "draft", 1)
        )
    else:
        con.execute("UPDATE revisions SET body='{}'")
    con.commit()
    before = list(con.iterdump())
    con.close()
    with pytest.raises(RouterError):
        inspect_run(instance, str(result["id"]), state_home=home)
    with pytest.raises(RouterError):
        recover_run(instance, str(result["id"]), state_home=home)
    con = sqlite3.connect(config.store_path)
    assert list(con.iterdump()) == before
    con.close()


def test_duplicate_completion_and_transition_are_idempotent(
    single_flow: tuple[Path, Path, Path],
) -> None:
    from dataclasses import replace

    from millrace.router.records import CommandResult

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    config = load_instance(instance, home)
    store = Store(config)
    try:
        completion = store.db.execute("SELECT * FROM completions").fetchone()
        invocation = str(completion["invocation_id"])
        exact = CommandResult(
            completion["stdout"],
            completion["stderr"],
            completion["transport"],
            completion["writer"],
            completion["diagnostic"],
            completion["exit_code"],
        )
        store.complete(invocation, exact)
        store.route(invocation)
        assert store.inspection(str(result["id"])) == result
        with pytest.raises(RouterError) as refusal:
            store.complete(invocation, replace(exact, stdout=b'{"outcome":"OTHER"}'))
        assert refusal.value.code == "conflicting_completion"
        assert store.inspection(str(result["id"])) == result
    finally:
        store.close()


@pytest.mark.parametrize("classification", [None, "semantic"])
def test_corrupt_parse_pair_refuses_without_mutation(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    classification: str | None,
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    node = data["planes"]["main"]["nodes"]["draft"]
    node["outcomes"].append("OTHER")
    node["on"]["OTHER"] = {"finish": "failed"}
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}
    original = Store.complete

    def after_completion(store: Store, *args: object) -> None:
        original(store, *args)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", after_completion)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    config = load_instance(instance, home)
    con = sqlite3.connect(config.store_path)
    con.execute(
        "UPDATE completions SET classification=?,outcome='OTHER'", (classification,)
    )
    con.commit()
    before = list(con.iterdump())
    con.close()
    owner = project / ".millrace-router-owner/owner.json"
    owner_before = owner.read_bytes()
    run = str(ids["run_id"])
    for operation in (inspect_run, recover_run, recover_run):
        with pytest.raises(RouterError) as refusal:
            operation(instance, run, state_home=home)
        assert refusal.value.code == "completion_integrity"
    con = sqlite3.connect(config.store_path)
    assert list(con.iterdump()) == before
    con.close()
    assert owner.read_bytes() == owner_before


@pytest.mark.parametrize(
    "corruption", ["missing", "unset", "unknown", "rejected", "orphan"]
)
def test_ref_admission_fact_corruption_refuses_without_repair(
    single_flow: tuple[Path, Path, Path], corruption: str
) -> None:
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    config = load_instance(instance, home)
    con = sqlite3.connect(config.store_path)
    body = json.loads(con.execute("SELECT body FROM invocations").fetchone()[0])
    if corruption == "missing":
        del body["ref_admission"]
    elif corruption == "unset":
        body["ref_admission"] = None
    elif corruption in {"unknown", "rejected"}:
        body["ref_admission"] = corruption
    else:
        con.execute("UPDATE completions SET classification=NULL,outcome=NULL")
    con.execute("UPDATE invocations SET body=?", (command_graph_json(body),))
    con.commit()
    before = list(con.iterdump())
    con.close()
    for operation in (inspect_run, recover_run):
        with pytest.raises(RouterError) as refusal:
            operation(instance, str(result["id"]), state_home=home)
        assert refusal.value.code == "completion_integrity"
    con = sqlite3.connect(config.store_path)
    assert list(con.iterdump()) == before
    con.close()


def test_live_activation_and_route_roll_back_together(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router.records import CommandResult

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}

    def invocation(*args: object) -> CommandResult:
        graph = json.loads(workflow.read_text())
        graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {"finish": "failed"}
        workflow.write_text(command_graph_json(graph))
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    route = Store.route

    def crash(store: Store, invocation_id: str) -> None:
        route(store, invocation_id)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr("millrace.router.engine.invoke", invocation)
        patch.setattr(Store, "route", crash)
        with pytest.raises(Crash):
            start_run(
                instance, state_home=home, edit_mode="live", on_started=ids.update
            )
    raw = inspect_run(instance, str(ids["run_id"]), state_home=home)
    assert raw["count"] == 1 and raw["completions"][0]["classification"] == "semantic"
    assert (
        raw["revision_id"] == raw["startup_revision_id"]
        and len(raw["candidates"]) == 1
        and raw["pending_seq"] == 1
        and raw["transitions"] == []
    )
    assert len(raw["activations"]) == 1
    finished = recover_run(instance, str(ids["run_id"]), state_home=home)
    assert (
        finished["status"] == "failed"
        and finished["count"] == 1
        and len(finished["transitions"]) == 1
    )
    assert finished["revision_id"] != finished["startup_revision_id"]
    assert (
        finished["activations"][-1]["version"]
        == finished["transitions"][0]["version"]
        == raw["state_version"] + 1
    )
    assert recover_run(instance, str(ids["run_id"]), state_home=home) == finished


def test_validation_reads_one_committed_snapshot(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    store = Store(control.load_instance(instance, home))
    original = store.db
    committed = False

    class ConcurrentCommit:
        def __getattr__(self, name: str) -> object:
            return getattr(original, name)

        def execute(self, sql: str, *args: object) -> object:
            nonlocal committed
            if sql.startswith("SELECT version FROM run_versions") and not committed:
                committed = True
                control.cancel_run(
                    instance, str(before["id"]), state_home=home, request_id=new_id()
                )
            return original.execute(sql, *args)

    monkeypatch.setattr(store, "db", ConcurrentCommit())
    try:
        store.validate()
        assert committed and not original.in_transaction
    finally:
        store.close()
    after = control.inspect_run(instance, str(before["id"]), state_home=home)
    assert after["status"] == "cancelled"
    assert after["state_version"] == before["state_version"] + 1


def test_request_validation_uses_one_snapshot(single_flow, monkeypatch):
    from millrace.router import control
    from millrace.router import control_requests as requests
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    config = control.load_instance(instance, home)
    holder, appender = Store(config), Store(config)
    request = requests.envelope(
        "pause_run", instance, before["id"], new_id(), None, {}, plane_id=None
    )
    requests.append(appender, request)
    original = requests.receipt_base
    fired = False

    def racing(body, status):
        nonlocal fired
        if body["request_id"] == request["request_id"] and not fired:
            fired = True
            with appender.transaction():
                requests.finish(appender, request, code="busy", retryable=True)
        return original(body, status)

    monkeypatch.setattr(requests, "receipt_base", racing)
    try:
        requests.validate_requests(holder)
        assert fired and not holder.db.in_transaction
        requests.validate_requests(holder)
    finally:
        holder.close()
        appender.close()


@pytest.mark.parametrize("table", ["states", "activations"])
def test_initial_authority_rows_are_required(single_flow, table):
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    final = start_run(instance, state_home=home)
    config = load_instance(instance, home)
    with sqlite3.connect(config.store_path) as db:
        db.execute(f"DELETE FROM {table} WHERE version=1")
    with pytest.raises(RouterError):
        inspect_run(instance, final["id"], state_home=home)


def test_running_state_cannot_carry_reason(single_flow):
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    final = start_run(instance, state_home=home)
    config = load_instance(instance, home)
    with sqlite3.connect(config.store_path) as db:
        assert db.execute(
            "UPDATE states SET reason='invented' WHERE status='running'"
        ).rowcount
    with pytest.raises(RouterError):
        inspect_run(instance, final["id"], state_home=home)
