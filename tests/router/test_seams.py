"""Pure recurring seam contracts; no broker or runtime dispatch."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest
from tests.router.test_planes import authored

from millrace.router.records import RouterError, action


def _seam_store(flow, *, consumer_action=None):
    import sys

    from tests.router.test_planes import initial_run

    workflow, project, _ = flow
    data = authored()
    data["bindings"]["python"]["executable"] = sys.executable
    if consumer_action is not None:
        data["planes"]["beta"]["nodes"]["step"]["on"]["READY"] = consumer_action
    for name in data["planes"]:
        (project / name).mkdir()
    workflow.write_text(json.dumps(data))
    store, run_id = initial_run(flow)
    store.recompute(run_id)  # same startup projection as run_engine
    return store, run_id


def _seam_completion(
    store, run_id, plane_id, *, stdout=b'{"outcome":"READY"}', transport="completed"
):
    from millrace.router.records import CommandResult

    assert store.eligible(run_id, plane_id)
    invocation = store.reserve(
        run_id, store.revision(str(store.run(run_id)["revision_id"])), plane_id
    )
    store.complete(
        invocation["id"],
        CommandResult(stdout, b"", transport, "stopped", exit_code=0),
    )
    raw = store.db.execute(
        "SELECT * FROM completions WHERE invocation_id=?", (invocation["id"],)
    ).fetchone()
    assert raw["classification"] is None and raw["stdout"] == stdout
    return invocation["id"]


def _seam_control(store, run_id, operation, payload):
    from millrace.router.control_requests import STOPS, append, envelope, finish
    from millrace.router.controller import Controller
    from millrace.router.engine import Signals
    from millrace.router.ownership import new_id

    request = envelope(
        operation,
        store.config.instance_id,
        run_id,
        new_id(),
        None if operation in STOPS else store.run(run_id)["state_version"],
        payload,
        plane_id="beta",
    )
    with store.transaction():
        append(store, request)
        result = Controller(store, Signals()).effect(request, store.run(run_id))
        finish(store, request, result=result)
    return result


def test_pinned_manual_retry_reuses_input_and_refuses_prior_lineage(flow):
    from millrace.router.store import Store

    store, run_id = _seam_store(
        flow, consumer_action={"pause": {"reason": "review", "resume": "step"}}
    )
    config = store.config
    try:
        producer = _seam_completion(store, run_id, "alpha")
        store.route(producer)
        first = _seam_completion(store, run_id, "beta")
        store.route(first)
        assert (
            _seam_control(
                store,
                run_id,
                "retry_invocation",
                {"invocation_id": first, "reason": "retry current input"},
            )["action"]
            == "retrying"
        )
        second = _seam_completion(store, run_id, "beta")
        store.route(second)
        before = store.inspection(run_id)
        assert before["seam_acknowledgements"] == []
        inputs = [
            row["launch"]["seam_input"]
            for row in before["invocations"]
            if row["plane_id"] == "beta"
        ]
        assert inputs[0]["content"] == inputs[1]["content"]
        assert inputs[0]["path"] != inputs[1]["path"]
        with pytest.raises(RouterError, match="seam_input_pinned"):
            _seam_control(
                store,
                run_id,
                "redirect_plane",
                {"node_id": "step", "reason": "cannot replace pinned input"},
            )
        with pytest.raises(RouterError, match="seam_input_pinned"):
            _seam_control(
                store,
                run_id,
                "retry_invocation",
                {"invocation_id": first, "reason": "stale invocation lineage"},
            )
        assert store.inspection(run_id) == before
        assert (
            _seam_control(
                store,
                run_id,
                "retry_invocation",
                {"invocation_id": second, "reason": "current lineage remains legal"},
            )["action"]
            == "retrying"
        )
        third = _seam_completion(store, run_id, "beta")
        store.route(third)
    finally:
        store.close()
    reopened = Store(config, readonly=True)
    try:
        state = reopened.inspection(run_id)
        assert state["count"] == 4 and state["seam_acknowledgements"] == []
        assert state["planes"]["beta"]["selected"] == {"ready": 1}
    finally:
        reopened.close()


def test_cancel_pinned_consumer_retains_selection_without_ack_or_revival(flow):
    from millrace.router.store import Store

    store, run_id = _seam_store(
        flow, consumer_action={"pause": {"reason": "review", "resume": "step"}}
    )
    config = store.config
    try:
        producer = _seam_completion(store, run_id, "alpha")
        store.route(producer)
        consumer = _seam_completion(store, run_id, "beta")
        store.route(consumer)
        before = store.inspection(run_id)
        assert before["planes"]["beta"]["status"] == "paused"
        assert before["planes"]["beta"]["selected"] == {"ready": 1}
        assert before["planes"]["beta"]["progress"] == {}
        assert before["seam_acknowledgements"] == []
        _seam_control(store, run_id, "cancel_plane", {})
        cancelled = store.inspection(run_id)
        assert cancelled["status"] == "cancelled"
        assert cancelled["planes"]["alpha"]["status"] == "succeeded"
        assert cancelled["planes"]["beta"]["status"] == "cancelled"
        assert cancelled["planes"]["beta"]["selected"] == {"ready": 1}
        assert cancelled["planes"]["beta"]["progress"] == {}
        assert cancelled["planes"]["beta"]["active_invocation"] is None
        assert cancelled["seam_acknowledgements"] == []
        for key in (
            "invocations",
            "completions",
            "transitions",
            "seam_occurrences",
            "seam_selections",
            "count",
            "cap",
            "deadline",
        ):
            assert cancelled[key] == before[key]
    finally:
        store.close()
    store = Store(config)
    try:
        assert store.inspection(run_id) == cancelled
        for operation, payload in (
            ("resume_plane", {}),
            ("retry_invocation", {"invocation_id": consumer, "reason": "terminal"}),
        ):
            with pytest.raises(RouterError, match="run_terminal"):
                _seam_control(store, run_id, operation, payload)
            assert store.inspection(run_id) == cancelled
    finally:
        store.close()
    reopened = Store(config, readonly=True)
    try:
        assert reopened.inspection(run_id) == cancelled
    finally:
        reopened.close()


def test_manual_retry_of_acknowledged_occurrence_refuses_without_mutation(flow):
    from millrace.router.store import Store

    store, run_id = _seam_store(flow, consumer_action={"pause": {"reason": "done"}})
    config = store.config
    try:
        producer = _seam_completion(store, run_id, "alpha")
        store.route(producer)
        consumer = _seam_completion(store, run_id, "beta")
        store.route(consumer)
        before = store.inspection(run_id)
        assert len(before["seam_acknowledgements"]) == 1
        with pytest.raises(RouterError, match="occurrence_already_acknowledged"):
            _seam_control(
                store,
                run_id,
                "retry_invocation",
                {"invocation_id": consumer, "reason": "already consumed"},
            )
        assert store.inspection(run_id) == before
    finally:
        store.close()
    reopened = Store(config, readonly=True)
    try:
        assert reopened.inspection(run_id) == before
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "occurrence_missing",
        "occurrence_producer",
        "occurrence_completion",
        "occurrence_transition",
        "occurrence_version",
        "occurrence_revision",
        "occurrence_result",
        "selection_missing",
        "selection_plane",
        "selection_node",
        "selection_version",
        "selection_body",
        "ack_missing",
        "ack_completion",
        "ack_version",
        "occurrence_sequence",
        "selection_sequence",
        "ack_sequence",
    ],
)
def test_reload_rejects_corrupt_seam_relations_with_unchanged_plane_projection(
    flow, corruption
):
    import sqlite3

    from millrace.router.store import Store

    store, run_id = _seam_store(flow)
    producer = _seam_completion(store, run_id, "alpha")
    store.route(producer)
    consumer = _seam_completion(store, run_id, "beta")
    store.route(consumer)
    config = store.config
    valid = store.inspection(run_id)
    store.close()
    reopened = Store(config, readonly=True)
    assert reopened.inspection(run_id) == valid
    reopened.close()
    table = {
        "occurrence": "seam_occurrences",
        "selection": "seam_selections",
        "ack": "seam_acknowledgements",
    }[corruption.split("_")[0]]
    with sqlite3.connect(config.store_path) as db:
        projections = list(db.execute("SELECT * FROM planes ORDER BY plane_id"))
        if corruption.endswith("_missing"):
            db.execute(f"DELETE FROM {table}")
        elif corruption in {
            "occurrence_revision",
            "occurrence_result",
            "selection_body",
        }:
            body = json.loads(db.execute(f"SELECT body FROM {table}").fetchone()[0])
            if corruption == "occurrence_revision":
                body["producing_revision"] = "0" * 64
            elif corruption == "occurrence_result":
                body["result"] = {"outcome": "READY", "summary": "forged payload"}
            else:
                body["producer_workspace"] = "/different/captured/producer"
            db.execute(f"UPDATE {table} SET body=?", (json.dumps(body),))
        else:
            field, value = {
                "occurrence_producer": ("producer_invocation_id", consumer),
                "occurrence_completion": ("completion_id", consumer),
                "occurrence_transition": ("transition_id", consumer),
                "occurrence_version": ("version", 1),
                "selection_plane": ("consumer_plane_id", "alpha"),
                "selection_node": ("consumer_node_id", "wrong"),
                "selection_version": ("version", 1),
                "ack_completion": ("completion_id", producer),
                "ack_version": ("version", 1),
                "occurrence_sequence": ("sequence", 2),
                "selection_sequence": ("sequence", 2),
                "ack_sequence": ("sequence", 2),
            }[corruption]
            db.execute(f"UPDATE {table} SET {field}=?", (value,))
        assert list(db.execute("SELECT * FROM planes ORDER BY plane_id")) == projections
        corrupt = list(db.iterdump())
    for readonly in (True, False):
        expected = (
            "store_integrity"
            if corruption
            in {
                "occurrence_missing",
                "selection_missing",
                "occurrence_sequence",
                "selection_sequence",
                "ack_sequence",
            }
            else "seam_integrity"
        )
        with pytest.raises(RouterError, match=expected):
            Store(config, readonly=readonly)
        with sqlite3.connect(config.store_path) as db:
            assert list(db.iterdump()) == corrupt


@pytest.mark.parametrize("stage", ["publication", "selection", "acknowledgement"])
@pytest.mark.parametrize("window", ["before", "inside", "after"])
def test_seam_commit_crash_windows_reload_once(flow, monkeypatch, stage, window):
    from millrace.router.store import Store

    store, run_id = _seam_store(flow)
    producer = _seam_completion(store, run_id, "alpha")
    target = producer
    if stage != "publication":
        store.route(producer)
    if stage == "acknowledgement":
        target = _seam_completion(store, run_id, "beta")
    config = store.config

    class Crash(BaseException):
        pass

    def operation():
        if stage == "selection":
            return store.eligible(run_id, "beta")
        return store.route(target)

    if window == "inside":
        from contextlib import contextmanager

        original = Store.save_plane
        transaction = Store.transaction

        def crash(self, run, plane, kind, cause):
            original(self, run, plane, kind, cause)
            if kind == "routed":
                # Publication/ack SQL already ran, but its transaction is open.
                assert self.db.in_transaction
                raise Crash

        @contextmanager
        def crash_selection(self):
            with transaction(self):
                yield
                if self.db.execute("SELECT 1 FROM seam_selections").fetchone():
                    # Both selected event and relation exist before COMMIT.
                    assert self.db.in_transaction
                    raise Crash

        with monkeypatch.context() as patch:
            if stage == "selection":
                patch.setattr(Store, "transaction", crash_selection)
            else:
                patch.setattr(Store, "save_plane", crash)
            with pytest.raises(Crash):
                operation()
    elif window == "after":
        operation()
    store.close()  # abrupt owner loss at the selected committed boundary

    store = Store(config)
    try:
        before = store.inspection(run_id)
        committed = window == "after"
        expected = {
            "publication": (int(committed), 0, 0),
            "selection": (1, int(committed), 0),
            "acknowledgement": (1, 1, int(committed)),
        }[stage]
        assert (
            tuple(
                len(before[key])
                for key in (
                    "seam_occurrences",
                    "seam_selections",
                    "seam_acknowledgements",
                )
            )
            == expected
        )
        if stage != "selection":
            assert len(before["completions"]) == (
                2 if stage == "acknowledgement" else 1
            )
            store.route(target)
            store.route(target)
        else:
            assert store.eligible(run_id, "beta")
            assert store.eligible(run_id, "beta")
        if stage != "acknowledgement":
            consumer = _seam_completion(store, run_id, "beta")
            store.route(consumer)
            store.route(consumer)
        final = store.inspection(run_id)
        assert final["status"] == "succeeded" and final["count"] == 2
        assert [row["sequence"] for row in final["seam_occurrences"]] == [1]
        assert [row["sequence"] for row in final["seam_selections"]] == [1]
        assert [row["sequence"] for row in final["seam_acknowledgements"]] == [1]
        assert final["planes"]["beta"]["selected"] == {}
        assert final["planes"]["beta"]["progress"] == {"ready": 1}
    finally:
        store.close()
    reopened = Store(config, readonly=True)
    try:
        assert reopened.inspection(run_id) == final
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "result,transport,classification,published",
    [
        (b'{"outcome":"READY","summary":"usable"}', "completed", "semantic", True),
        (b'{"outcome":"OTHER"}', "completed", "unknown", False),
        (b"not-json", "completed", "protocol_failure", False),
        (b'{"outcome":"READY"}', "execution_failure", "execution_failure", False),
    ],
)
def test_only_committed_semantic_outcome_publishes(
    flow, result, transport, classification, published
):
    from millrace.router.store import Store

    store, run_id = _seam_store(flow)
    config = store.config
    try:
        producer = _seam_completion(
            store, run_id, "alpha", stdout=result, transport=transport
        )
        assert store.inspection(run_id)["seam_occurrences"] == []
        store.route(producer)
        store.route(producer)
        state = store.inspection(run_id)
        assert state["completions"][0]["classification"] == classification
        assert len(state["seam_occurrences"]) == int(published)
        assert store.eligible(run_id, "beta") is published
        if published:
            consumer = _seam_completion(store, run_id, "beta")
            store.route(consumer)
        else:
            assert store.plane_states(run_id)["beta"]["pause_reason"] == "seam_input"
    finally:
        store.close()
    reopened = Store(config, readonly=True)
    try:
        assert len(reopened.inspection(run_id)["seam_occurrences"]) == int(published)
    finally:
        reopened.close()


def test_seam_endpoint_feedback_and_ack_contract() -> None:
    from millrace.router.graph import parse_graph
    from millrace.router.seams import acknowledges

    data = authored()
    data["seams"]["return"] = {
        "producer": {"plane": "beta", "node": "step", "outcome": "READY"},
        "consumer": {"plane": "alpha", "node": "step"},
    }
    assert len(parse_graph(json.dumps(data).encode()).seams) == 2
    for value, expected in [
        ({"retry": {"limit": 1, "exhausted": {"finish": "failed"}}}, False),
        ({"pause": {"reason": "input", "resume": "step"}}, False),
        ({"goto": "step"}, True),
        ({"finish": "succeeded"}, True),
        ({"pause": {"reason": "input", "resume": "other"}}, True),
        ({"pause": {"reason": "input"}}, True),
    ]:
        assert acknowledges(action(value), "step") is expected
    assert acknowledges(None, "step") is False  # operator/run gates are not transitions


def test_seam_input_public_reference_and_reserved_names(tmp_path: Path) -> None:
    from millrace.router.execution import (
        InvocationRequest,
        LaunchPlan,
        SeamInput,
        _admit,
    )
    from millrace.router.seams import build_input

    content = {
        "format": "millrace-seam-input/v1",
        "run_id": "1" * 32,
        "consumer_plane_id": "beta",
        "consumer_node_id": "step",
        "seam_id": "ready",
        "sequence": 1,
        "producer_plane_id": "alpha",
        "producer_node_id": "step",
        "producer_invocation_id": "2" * 32,
        "producing_revision": "a" * 64,
        "completion_id": "2" * 32,
        "producer_workspace": "/retained/disappeared/alpha",
        "result": {"outcome": "READY", "summary": "done", "refs": ["result.txt"]},
    }
    seam = build_input(tmp_path / "invocation/seam-input.json", content)
    assert isinstance(seam, SeamInput) and not seam.path.exists()
    assert json.loads(seam.content) == content
    assert seam.sha256 == hashlib.sha256(seam.content).hexdigest()
    with pytest.raises(dataclasses.FrozenInstanceError):
        seam.sequence = 2
    request = InvocationRequest(
        invocation_id="3" * 32,
        plane_id="beta",
        node_id="step",
        producing_revision="a" * 64,
        binding_id="command",
        adapter_id="command",
        adapter_contract="millrace-command/v1",
        executable="/python",
        binding_config={},
        node_config={},
        runtime={},
        workflow={},
        plane={},
        common_settings={},
        files={},
        host_paths={},
        environment_names=(),
        outcomes=("READY",),
        result_contract="json-v1",
        project_root=tmp_path,
        invocation_root=seam.path.parent,
        seam_input=seam,
    )
    plan = LaunchPlan(
        request.invocation_id,
        "command",
        "millrace-command/v1",
        ("/python",),
        {},
        {},
        {},
        {},
    )
    assert _admit(plan, request)["seam_input"]["sha256"] == seam.sha256
    for name in ("seam-input.json", "SEAM-INPUT.JSON", "seam-input.json/child"):
        with pytest.raises(RouterError):
            _admit(
                dataclasses.replace(plan, generated_controls={name: b"bad"}), request
            )
    with pytest.raises(RouterError):
        _admit(
            dataclasses.replace(plan, environment={"MILLRACE_SEAM_INPUT": "bad"}),
            request,
        )
    with pytest.raises(RouterError):
        _admit(
            plan,
            dataclasses.replace(request, environment_names=("MILLRACE_SEAM_INPUT",)),
        )
    with pytest.raises(RouterError):
        _admit(
            plan,
            dataclasses.replace(
                request, seam_input=dataclasses.replace(seam, sha256="0" * 64)
            ),
        )
    with pytest.raises(RouterError):
        _admit(plan, dataclasses.replace(request, plane_id="gamma"))
    with pytest.raises(RouterError):
        _admit(
            plan,
            dataclasses.replace(
                request, seam_input=dataclasses.replace(seam, seam_id="other")
            ),
        )


def test_consumer_pause_resume_self_reuses_selection_and_goto_acknowledges(single_flow):
    import copy
    import json

    from millrace.router import control
    from millrace.router.ownership import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    base = graph["planes"].pop("main")
    graph["limits"]["max_invocations"] = 8
    graph["planes"] = {
        name: {**copy.deepcopy(base), "workspace": name}
        for name in ("producer", "consumer")
    }
    for name in graph["planes"]:
        (project / name).mkdir()
    producer = graph["planes"]["producer"]["nodes"]["draft"]
    producer["on"]["READY"] = {
        "retry": {"limit": 1, "exhausted": {"finish": "succeeded"}}
    }
    consumer = graph["planes"]["consumer"]["nodes"]["draft"]
    consumer["invoke"]["config"] = {"script": "consume.py"}
    consumer["on"]["READY"] = {"pause": {"reason": "review", "resume": "draft"}}
    graph["control_files"].append("consume.py")
    graph["seams"] = {
        "handoff": {
            "producer": {"plane": "producer", "node": "draft", "outcome": "READY"},
            "consumer": {"plane": "consumer", "node": "draft"},
        }
    }
    (workflow.parent / "consume.py").write_text(
        "import json,os\nfrom pathlib import Path\n"
        "item=json.loads(Path(os.environ['MILLRACE_SEAM_INPUT']).read_text())\n"
        "assert item['consumer_plane_id']=='consumer'\n"
        "assert Path(item['producer_workspace']).name=='producer'\n"
        "p=Path('seen');p.open('a').write(str(item['sequence'])+'\\n')\n"
        "print(json.dumps({'outcome':'READY'}))\n"
    )
    workflow.write_text(json.dumps(graph))
    made = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    instance = made["instance_id"]
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        edit_mode="live",
    )
    run_id = started["run_id"]
    before = control.inspect_run(instance, run_id, state_home=home)
    assert before["status"] == "paused" and before["run_gate"] is None
    producer_ids = [
        row["id"] for row in before["invocations"] if row["plane_id"] == "producer"
    ]
    assert len(producer_ids) == len(set(producer_ids)) == 2
    assert [
        row["producer_invocation_id"] for row in before["seam_occurrences"]
    ] == producer_ids
    assert all(
        row["transition_id"] == row["completion_id"] == row["producer_invocation_id"]
        for row in before["seam_occurrences"]
    )
    assert len(before["seam_occurrences"]) == 2
    assert len(before["seam_selections"]) == 1 and before["seam_acknowledgements"] == []
    assert before["planes"]["consumer"]["selected"] == {"handoff": 1}
    consumer["on"]["READY"] = {"goto": "draft"}
    workflow.write_text(json.dumps(graph))
    resumed = control.resume_plane(
        instance,
        run_id,
        plane_id="consumer",
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
    )
    assert resumed["status"] == "applied", resumed
    after = control.inspect_run(instance, run_id, state_home=home)
    assert (project / "consumer" / "seen").read_text().splitlines() == ["1", "1", "2"]
    assert [row["sequence"] for row in after["seam_acknowledgements"]] == [1, 2]
    assert after["planes"]["consumer"]["pause_reason"] == "seam_exhausted"
    assert after["planes"]["consumer"]["selected"] == {}
    assert after["planes"]["consumer"]["progress"] == {"handoff": 2}
    assert after["count"] == 5 and after["run_gate"] is None
    assert after["seam_occurrences"] == before["seam_occurrences"]
    assert control.inspect_run(instance, run_id, state_home=home) == after


def test_recurring_ping_pong_feedback_retry_restart(single_flow):
    import copy
    import json

    from millrace.router import control
    from millrace.router.ownership import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    base = graph["planes"].pop("main")
    template = base["nodes"]["draft"]
    emit = copy.deepcopy(template)
    emit["invoke"]["config"] = {"script": "emit.py"}
    emit["outcomes"] = ["READY", "END"]
    emit["on"] = {"READY": {"goto": "consume"}, "END": {"finish": "succeeded"}}
    consume = copy.deepcopy(template)
    consume["invoke"]["config"] = {"script": "consume.py"}
    consume["on"] = {"READY": {"goto": "emit"}}
    beta = copy.deepcopy(consume)
    beta["on"] = {
        "READY": {"retry": {"limit": 1, "exhausted": {"finish": "succeeded"}}}
    }
    graph["planes"] = {
        "alpha": {
            "start": "emit",
            "workspace": "alpha",
            "nodes": {"emit": emit, "consume": consume},
        },
        "beta": {"start": "consume", "workspace": "beta", "nodes": {"consume": beta}},
    }
    for plane in graph["planes"].values():
        for node_id, node in plane["nodes"].items():
            node["on_error"] = {"pause": {"reason": "error", "resume": node_id}}
            node["on_unknown"] = {"pause": {"reason": "unknown", "resume": node_id}}
    for name in graph["planes"]:
        (project / name).mkdir()
    graph["limits"]["max_invocations"] = 8
    graph["control_files"] += ["emit.py", "consume.py"]
    graph["seams"] = {
        "alpha_to_beta": {
            "producer": {"plane": "alpha", "node": "emit", "outcome": "READY"},
            "consumer": {"plane": "beta", "node": "consume"},
        },
        "beta_to_alpha": {
            "producer": {"plane": "beta", "node": "consume", "outcome": "READY"},
            "consumer": {"plane": "alpha", "node": "consume"},
        },
    }
    (workflow.parent / "emit.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "p=Path('emits');n=int(p.read_text())+1 if p.exists() else 1;"
        "p.write_text(str(n))\n"
        "print(json.dumps({'outcome':'READY' if n==1 else 'END'}))\n"
    )
    (workflow.parent / "consume.py").write_text(
        "import json,os\nfrom pathlib import Path\n"
        "item=json.loads(Path(os.environ['MILLRACE_SEAM_INPUT']).read_text())\n"
        "assert item['consumer_plane_id']==Path.cwd().name\n"
        "Path('seen').open('a').write(str(item['sequence'])+'\\n')\n"
        "print(json.dumps({'outcome':'READY'}))\n"
    )
    workflow.write_text(json.dumps(graph))
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
    )
    result = control.inspect_run(
        made["instance_id"], started["run_id"], state_home=home
    )
    assert result["status"] == "succeeded" and result["count"] == 5
    assert (project / "beta" / "seen").read_text().splitlines() == ["1", "1"]
    assert (project / "alpha" / "seen").read_text().splitlines() == ["1"]
    assert (
        len(result["seam_occurrences"]) == 3
        and len(result["seam_acknowledgements"]) == 2
    )
    beta_inputs = [
        row["launch"]["seam_input"]
        for row in result["invocations"]
        if row["plane_id"] == "beta"
    ]
    assert beta_inputs[0]["content"] == beta_inputs[1]["content"]
    assert beta_inputs[0]["path"] != beta_inputs[1]["path"]
    replay = control.recover_run(
        made["instance_id"],
        started["run_id"],
        state_home=home,
        request_id=new_id(),
        expected_state_version=result["state_version"],
    )
    assert replay["action"] == "already_terminal"
    after = control.inspect_run(made["instance_id"], started["run_id"], state_home=home)
    assert (
        after["count"] == 5 and after["seam_occurrences"] == result["seam_occurrences"]
    )
    assert after["seam_acknowledgements"] == result["seam_acknowledgements"]


@pytest.mark.parametrize("required", ["failed", "succeeded"])
def test_terminal_dependency_restart(single_flow, required):
    import copy
    import json

    from millrace.router import control
    from millrace.router.ownership import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    base = graph["planes"].pop("main")
    graph["planes"] = {
        name: {**copy.deepcopy(base), "workspace": name} for name in ("alpha", "beta")
    }
    graph["planes"]["alpha"]["nodes"]["draft"]["on"]["READY"] = {"finish": "failed"}
    graph["planes"]["beta"]["nodes"]["draft"]["requires_terminal"] = [
        {"plane": "alpha", "status": required}
    ]
    for name in graph["planes"]:
        (project / name).mkdir()
    workflow.write_text(json.dumps(graph))
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
    )
    result = control.inspect_run(
        made["instance_id"], started["run_id"], state_home=home
    )
    assert result["planes"]["alpha"]["status"] == "failed"
    if required == "failed":
        assert result["status"] == "failed" and result["count"] == 2
        assert result["planes"]["beta"]["status"] == "succeeded"
        alpha_transition = next(
            row for row in result["transitions"] if row["plane_id"] == "alpha"
        )
        beta_start = next(
            row for row in result["invocations"] if row["plane_id"] == "beta"
        )
        assert beta_start["version"] > alpha_transition["version"]
    else:
        assert result["status"] == "paused" and result["count"] == 1
        assert result["planes"]["beta"]["pause_reason"] == "dependency_failed"
        assert result["run_gate"] is None
    recovered = control.recover_run(
        made["instance_id"],
        started["run_id"],
        state_home=home,
        request_id=new_id(),
        expected_state_version=result["state_version"],
    )
    assert recovered["status"] == "applied"
    after = control.inspect_run(made["instance_id"], started["run_id"], state_home=home)
    assert after["planes"] == result["planes"] and after["count"] == result["count"]
    assert after["transitions"] == result["transitions"]
