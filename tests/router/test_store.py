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
    invocation = finished["invocations"][0]
    assert invocation["revision_id"] == finished["startup_revision_id"]
    assert (
        invocation["launch_settings"]["common"]["producing_revision"]
        == invocation["revision_id"]
    )
    assert (
        finished["activations"][-1]["version"]
        == finished["transitions"][0]["version"]
        == raw["state_version"] + 1
    )
    assert recover_run(instance, str(ids["run_id"]), state_home=home) == finished

    config = load_instance(instance, home)
    with sqlite3.connect(config.store_path) as connection:
        durable = list(connection.iterdump())
    assert inspect_run(instance, str(ids["run_id"]), state_home=home) == finished
    with sqlite3.connect(config.store_path) as connection:
        assert list(connection.iterdump()) == durable


@pytest.mark.parametrize(
    "corruption", ["revision_bytes", "launch_projection", "launch_settings", "seam"]
)
def test_validation_rechecks_authority_on_same_connection(single_flow, corruption):
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    final = start_run(instance, state_home=home)
    store = Store(load_instance(instance, home))
    try:
        store.validate()
        assert not store.db.in_transaction
        with sqlite3.connect(store.config.store_path) as writer:
            if corruption == "revision_bytes":
                writer.execute("UPDATE revisions SET body='{}'")
            else:
                raw = writer.execute("SELECT body FROM invocations").fetchone()[0]
                body = json.loads(raw)
                if corruption == "launch_projection":
                    body["launch"]["argv"].append("changed")
                elif corruption == "launch_settings":
                    body["launch_settings"]["common"]["producing_revision"] = "0" * 64
                else:
                    body["launch"]["seam_input"] = {
                        "path": str(project / "unexpected-seam"),
                        "content": "e30=", "sha256": "0" * 64, "seam_id": "unexpected",
                        "sequence": 1, "producer_invocation_id": "0" * 32,
                    }
                writer.execute("UPDATE invocations SET body=?", (json.dumps(body),))
        before = list(store.db.iterdump())
        with pytest.raises(RouterError):
            store.validate()
        assert not store.db.in_transaction
        assert list(store.db.iterdump()) == before
        assert final["status"] == "succeeded"
    finally:
        store.close()


@pytest.mark.parametrize("authority", ["revision", "host"])
def test_standalone_launch_rereads_authority(single_flow, authority):
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    final = start_run(instance, state_home=home)
    store = Store(load_instance(instance, home))
    try:
        raw = store.db.execute("SELECT body FROM invocations").fetchone()[0]
        body = json.loads(raw)
        assert list(store.launch(body).argv) == body["argv"]
        if authority == "revision":
            store.db.execute("UPDATE revisions SET body='{}'")
        else:
            run = store.run(str(final["id"]))
            run["host_settings"]["operator_policy"] = {"timeout_ceiling_seconds": 0.5}
            store.db.execute("UPDATE runs SET body=?", (json.dumps(run),))
        with pytest.raises(RouterError):
            store.launch(body)
    finally:
        store.close()


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


@pytest.mark.parametrize(
    "limits,cap,elapsed",
    [({}, None, None), ({"max_invocations": 3}, 3, None),
     ({"max_elapsed_seconds": 20}, None, 20),
     ({"max_invocations": 3, "max_elapsed_seconds": 20}, 3, 20)],
)
def test_v8_required_nullable_capture_integrity(single_flow, limits, cap, elapsed):
    import math

    from tests.router.support import optional_graph_json

    workflow, project, home = single_flow
    workflow.write_text(optional_graph_json(json.loads(workflow.read_text()), limits))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == "succeeded" and result["count"] == 1
    with sqlite3.connect(load_instance(instance, home).store_path) as db:
        assert dict(db.execute("SELECT key,value FROM meta"))["format"] == (
            "millrace-router-store/v8"
        )
    for key in ("startup_cap", "cap"):
        assert key in result and result[key] == cap
    for key in ("startup_elapsed", "max_elapsed"):
        assert key in result and result[key] == elapsed
    deadline = None if elapsed is None else result["started_at"] + elapsed
    for key in ("startup_deadline", "deadline"):
        assert key in result and result[key] == deadline
    invocation = result["invocations"][0]
    assert "run_deadline" in invocation and invocation["run_deadline"] == deadline
    assert math.isfinite(invocation["deadline"])
    assert invocation["deadline"] == (
        invocation["created_at"] + 1 if deadline is None
        else min(invocation["created_at"] + 1, deadline)
    )
    assert recover_run(instance, str(result["id"]), state_home=home) == result


@pytest.mark.parametrize(
    "field", ["startup_cap", "startup_elapsed", "startup_deadline", "cap",
              "max_elapsed", "deadline", "run_deadline", "invocation_deadline"],
)
@pytest.mark.parametrize("mutation", ["missing", "forged_numeric"])
def test_v8_nullable_corruption_refuses_without_mutation(single_flow, field, mutation):
    from tests.router.support import optional_graph_json

    workflow, project, home = single_flow
    workflow.write_text(optional_graph_json(json.loads(workflow.read_text()), {}))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    database = load_instance(instance, home).store_path
    table = (
        "invocations" if field in {"run_deadline", "invocation_deadline"} else "runs"
    )
    key = "deadline" if field == "invocation_deadline" else field
    with sqlite3.connect(database) as db:
        body = json.loads(db.execute(f"SELECT body FROM {table}").fetchone()[0])
        if mutation == "missing":
            del body[key]
        else:
            body[key] = result["started_at"] + 100 if "deadline" in key else 5
        db.execute(f"UPDATE {table} SET body=?", (json.dumps(body),))
    before = database.read_bytes()
    for operation in (inspect_run, recover_run):
        with pytest.raises(RouterError):
            operation(instance, str(result["id"]), state_home=home)
        assert database.read_bytes() == before



@pytest.mark.parametrize("version", ["v3", "v4"])
@pytest.mark.parametrize("corruption", [None, "body_earlier", "body_later", "anchor",
                                      "invalid_anchor", "nonfinite_anchor"])
def test_v8_independent_start_anchor_integrity(single_flow, version, corruption):
    from tests.router.support import optional_graph_json

    workflow, project, home = single_flow
    if version == "v4":
        workflow.write_text(optional_graph_json(json.loads(workflow.read_text()), {}))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    run = str(result["id"])
    database = load_instance(instance, home).store_path
    with sqlite3.connect(database) as db:
        body, anchor = db.execute("SELECT body,started_at FROM runs").fetchone()
        body = json.loads(body)
        assert anchor == body["started_at"] == result["started_at"]
        if corruption is None:
            assert recover_run(instance, run, state_home=home) == result
            assert db.execute("SELECT started_at FROM runs").fetchone()[0] == anchor
            return
        if corruption.startswith("body"):
            created = result["invocations"][0]["created_at"]
            body["started_at"] = (
                anchor - 1 if corruption == "body_earlier"
                else anchor + (created - anchor) / 2
            )
            assert body["started_at"] < created
            db.execute("UPDATE runs SET body=?", (json.dumps(body),))
        else:
            changed = {"anchor": anchor - 1, "invalid_anchor": "invalid",
                       "nonfinite_anchor": float("inf")}[corruption]
            db.execute("UPDATE runs SET started_at=?", (changed,))
    before = database.read_bytes()
    for operation in (inspect_run, recover_run):
        with pytest.raises(RouterError):
            operation(instance, run, state_home=home)
        assert database.read_bytes() == before


@pytest.mark.parametrize("empty", [False, True])
def test_v8_missing_start_anchor_refuses_even_empty_store(single_flow, empty):
    from millrace.router import control

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    if not empty:
        start_run(instance, state_home=home)
    database = load_instance(instance, home).store_path
    with sqlite3.connect(database) as db:
        db.execute("ALTER TABLE runs DROP COLUMN started_at")
    before = database.read_bytes()
    with pytest.raises(RouterError) as refused:
        control.inspect_instance(instance, state_home=home)
    assert refused.value.code == "store_integrity"
    assert database.read_bytes() == before


def test_v8_start_anchor_rolls_back_with_run_creation(single_flow, monkeypatch):
    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    original = Store.create_run

    def crash_after_insert(store, *args):
        created = original(store, *args)
        row = store.db.execute("SELECT started_at,body FROM runs").fetchone()
        assert row[0] == json.loads(row[1])["started_at"] == created["started_at"]
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "create_run", crash_after_insert)
        with pytest.raises(Crash):
            control.start_run(instance, state_home=home, request_id=new_id(),
                              expected_state_version=1)
    database = load_instance(instance, home).store_path
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0



@pytest.mark.parametrize("declaration", ["REAL", "TEXT NOT NULL"])
def test_empty_v8_requires_declared_start_anchor_schema(single_flow, declaration):
    from millrace.router import control

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    database = load_instance(instance, home).store_path
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        db.execute("DROP TABLE runs")
        db.execute(
            "CREATE TABLE runs(id TEXT PRIMARY KEY, revision_id TEXT NOT NULL "
            "REFERENCES revisions(id), hold_id TEXT NOT NULL UNIQUE, "
            f"body TEXT NOT NULL, started_at {declaration})"
        )
    before = database.read_bytes()
    with pytest.raises(RouterError) as refused:
        control.inspect_instance(instance, state_home=home)
    assert refused.value.code == "store_integrity"
    assert database.read_bytes() == before



@pytest.mark.parametrize("field", ["startup_cap", "startup_elapsed", "startup_deadline",
                                   "cap", "max_elapsed", "deadline", "run_deadline"])
def test_v8_finite_capture_cannot_be_erased(single_flow, field):
    from tests.router.support import optional_graph_json

    workflow, project, home = single_flow
    workflow.write_text(optional_graph_json(json.loads(workflow.read_text()), {
        "max_invocations": 3, "max_elapsed_seconds": 20,
    }))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    table = "invocations" if field == "run_deadline" else "runs"
    database = load_instance(instance, home).store_path
    with sqlite3.connect(database) as db:
        body = json.loads(db.execute(f"SELECT body FROM {table}").fetchone()[0])
        body[field] = None
        db.execute(f"UPDATE {table} SET body=?", (json.dumps(body),))
    before = database.read_bytes()
    for operation in (inspect_run, recover_run):
        with pytest.raises(RouterError):
            operation(instance, str(result["id"]), state_home=home)
        assert database.read_bytes() == before



@pytest.mark.parametrize("empty", [False, True])
def test_v8_relabelled_as_v7_refuses_without_mutation(single_flow, empty):
    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    # Finite-v3 authored bytes deliberately cannot expose the false version by
    # nullable capture alone. The independent schema identity must reject it.
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    if not empty:
        start_run(instance, state_home=home)
    database = load_instance(instance, home).store_path
    with sqlite3.connect(database) as db:
        assert "started_at" in {r[1] for r in db.execute("PRAGMA table_info(runs)")}
        db.execute(
            "UPDATE meta SET value='millrace-router-store/v7' WHERE key='format'"
        )
    before = database.read_bytes()
    for operation in (
        lambda: control.inspect_instance(instance, state_home=home),
        lambda: control.start_run(instance, state_home=home, request_id=new_id(),
                                  expected_state_version=1),
    ):
        with pytest.raises(RouterError) as refused:
            operation()
        assert refused.value.code == "store_integrity"
        assert database.read_bytes() == before
