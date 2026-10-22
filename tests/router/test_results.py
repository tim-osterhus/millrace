import json
from pathlib import Path

import pytest
from tests.router.support import (
    command_graph_json,
    create_instance,
    inspect_run,
    recover_run,
    start_run,
)


@pytest.mark.parametrize(
    "mode,classification,status",
    [
        ("good", "semantic", "succeeded"),
        ("stderr", "semantic", "succeeded"),
        ("nonzero", "execution_failure", "paused"),
        ("hugeout", "protocol_failure", "paused"),
        ("hugeerr", "semantic", "succeeded"),
        ("utf16", "protocol_failure", "paused"),
        ("invalidutf8", "protocol_failure", "paused"),
        ("duplicate", "protocol_failure", "paused"),
        ("trailing", "protocol_failure", "paused"),
        ("empty", "protocol_failure", "paused"),
        ("unknown", "unknown", "paused"),
    ],
)
def test_final_channel_and_launch_contract(
    single_flow: tuple[Path, Path, Path], mode: str, classification: str, status: str
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    data["planes"]["main"]["nodes"]["draft"]["invoke"]["args"] = [mode]
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == status
    assert result["count"] == 1
    completion = result["completions"][0]
    assert completion["classification"] == classification
    assert (
        len(completion["stdout"].encode()) <= 65538
    )  # replacement decoding can enlarge invalid bytes
    assert len(completion["stderr"]) <= 65536
    if mode == "unknown":
        assert completion["outcome"] == "MYSTERY"
    if mode == "hugeerr":
        assert completion["diagnostic"] == "stderr_truncated"
    before = inspect_run(instance, str(result["id"]), state_home=home)
    assert recover_run(instance, str(result["id"]), state_home=home) == before


@pytest.mark.parametrize(
    "payload",
    [
        {"outcome": "READY", "extra": True},
        {"outcome": "READY", "summary": 1},
        {"outcome": "READY", "refs": ["../outside"]},
        {"outcome": "READY", "refs": ["/absolute"]},
        {"outcome": "READY", "refs": [".millrace-router-owner/owner.json"]},
    ],
)
def test_result_ref_and_contract_refusals(
    single_flow: tuple[Path, Path, Path], payload: dict[str, object]
) -> None:
    workflow, project, home = single_flow
    (workflow.parent / "scripts/draft.py").write_text(
        "print(" + repr(command_graph_json(payload)) + ")\n"
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["completions"][0]["classification"] == "protocol_failure"
    assert result["status"] == "paused"


@pytest.mark.parametrize("outcome", ["BLOCKED", "TEST_FAILED"])
def test_declared_domain_failure_is_semantic(
    single_flow: tuple[Path, Path, Path], outcome: str
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    node = data["planes"]["main"]["nodes"]["draft"]
    node["outcomes"] = [outcome]
    node["on"] = {outcome: {"pause": {"reason": "domain_result"}}}
    workflow.write_text(command_graph_json(data))
    (workflow.parent / "scripts/draft.py").write_text(
        "print(" + repr(command_graph_json({"outcome": outcome})) + ")\n"
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == "paused"
    assert result["pause_reason"] == "no_runnable_planes"
    assert result["planes"]["main"]["status"] == "paused"
    assert result["planes"]["main"]["pause_reason"] == "domain_result"
    assert result["completions"][0]["transport"] == "completed"
    assert result["completions"][0]["classification"] == "semantic"
    assert result["completions"][0]["outcome"] == outcome
    assert result["retry_counts"]["main"] == {}


@pytest.mark.parametrize("ref", ["alias", "alias/owner.json"])
def test_result_ref_rejects_canonical_owner_alias(
    single_flow: tuple[Path, Path, Path], ref: str
) -> None:
    from millrace.router.graph import capture
    from millrace.router.records import parse_result

    workflow, project, _ = single_flow
    alias = project / "alias"
    alias.symlink_to(".millrace-router-owner", target_is_directory=True)
    node = capture(workflow, project).graph.planes["main"].nodes["draft"]
    raw = command_graph_json({"outcome": "READY", "refs": [ref]}).encode()
    assert parse_result(raw, node, project) == ("protocol_failure", None)
    alias.unlink()
    (project / "ordinary").mkdir()
    alias.symlink_to("ordinary", target_is_directory=True)
    assert parse_result(raw, node, project) == ("semantic", "READY")


@pytest.mark.parametrize("boundary", ["terminal", "result_commit", "parsed_commit"])
@pytest.mark.parametrize("target", ["outside", "owner"])
def test_result_ref_admission_boundaries_survive_context_changes(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    target: str,
) -> None:
    from millrace.router.records import CommandResult
    from millrace.router.store import Store

    class Crash(BaseException):
        pass

    workflow, project, home = single_flow
    (project / "inside.txt").write_text("ordinary data")
    outside = project.parent / "outside.txt"
    outside.write_text("other ordinary data")
    alias = project / "current.txt"
    alias.symlink_to("inside.txt")
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    raw = b'{"outcome":"READY","refs":["current.txt"]}'
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(raw, b"", "completed", "stopped", exit_code=0),
    )
    ids: dict[str, object] = {}
    if boundary != "terminal":
        method = "complete" if boundary == "result_commit" else "parsed"
        original = getattr(Store, method)

        def committed(store: Store, *args: object) -> None:
            original(store, *args)
            raise Crash()

        with monkeypatch.context() as patch:
            patch.setattr(Store, method, committed)
            with pytest.raises(Crash):
                start_run(instance, state_home=home, on_started=ids.update)
        unparsed = inspect_run(instance, str(ids["run_id"]), state_home=home)
        if boundary == "result_commit":
            assert unparsed["completions"][0]["classification"] is None
            assert unparsed["completions"][0]["outcome"] is None
            assert unparsed["invocations"][0]["ref_admission"] is None
        else:
            assert unparsed["completions"][0]["classification"] == "semantic"
            assert unparsed["invocations"][0]["ref_admission"] == "accepted"
    else:
        before = start_run(instance, state_home=home, on_started=ids.update)
        assert before["status"] == "succeeded"
    alias.unlink()
    alias.symlink_to(
        outside if target == "outside" else ".millrace-router-owner/owner.json"
    )
    run = str(ids["run_id"])
    if boundary == "terminal":
        assert inspect_run(instance, run, state_home=home) == before
    finished = recover_run(instance, run, state_home=home)
    assert finished["status"] == (
        "paused" if boundary == "result_commit" else "succeeded"
    )
    assert finished["count"] == 1
    assert finished["completions"][0]["classification"] == (
        "protocol_failure" if boundary == "result_commit" else "semantic"
    )
    assert finished["invocations"][0]["ref_admission"] == (
        "rejected" if boundary == "result_commit" else "accepted"
    )
    assert finished["completions"][0]["stdout"] == raw.decode()
    assert len(finished["transitions"]) == 1
    assert recover_run(instance, run, state_home=home) == finished
    assert inspect_run(instance, run, state_home=home) == finished
    assert (project / ".millrace-router-owner/owner.json").exists() is (
        boundary == "result_commit"
    )


@pytest.mark.parametrize("target", ["outside", "owner"])
def test_rejected_refs_do_not_become_success_after_context_change(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    from millrace.router.records import CommandResult

    workflow, project, home = single_flow
    (project / "inside.txt").write_text("ordinary data")
    outside = project.parent / "outside.txt"
    outside.write_text("other ordinary data")
    alias = project / "current.txt"
    alias.symlink_to(
        outside if target == "outside" else ".millrace-router-owner/owner.json"
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    raw = b'{"outcome":"READY","refs":["current.txt"]}'
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(raw, b"", "completed", "stopped", exit_code=0),
    )
    result = start_run(instance, state_home=home)
    assert result["status"] == "paused"
    assert result["completions"][0]["classification"] == "protocol_failure"
    assert result["completions"][0]["outcome"] is None
    assert result["completions"][0]["stdout"] == raw.decode()
    alias.unlink()
    alias.symlink_to("inside.txt")
    assert inspect_run(instance, str(result["id"]), state_home=home) == result
    assert recover_run(instance, str(result["id"]), state_home=home) == result


def test_ref_symlink_loop_records_immutable_rejection(single_flow, monkeypatch):
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    (project / "loop").symlink_to("loop")
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY","refs":["loop"]}',
            b"",
            "completed",
            "stopped",
            exit_code=0,
        ),
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    paused = start_run(instance, state_home=home)
    assert paused["completions"][0]["classification"] == "protocol_failure"
    (project / "loop").unlink()
    (project / "loop").write_text("later repair")
    assert (
        control.inspect_run(instance, paused["id"], state_home=home)["completions"]
        == paused["completions"]
    )
    control.cancel_run(instance, paused["id"], state_home=home, request_id=new_id())
    assert (
        control.inspect_run(instance, paused["id"], state_home=home)["status"]
        == "cancelled"
    )


def test_owner_case_alias_reference_is_rejected(single_flow, monkeypatch):
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    seen = []

    def invoke(*args):
        owner = project / ".millrace-router-owner/owner.json"
        alias = project / ".MILLRACE-ROUTER-OWNER/owner.json"
        if alias.exists():
            assert alias.samefile(owner)
            seen.append(True)
        return CommandResult(
            b'{"outcome":"READY","refs":[".MILLRACE-ROUTER-OWNER/owner.json"]}',
            b"",
            "completed",
            "stopped",
            exit_code=0,
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    paused = start_run(instance, state_home=home)
    assert paused["completions"][0]["classification"] == "protocol_failure"
    control.cancel_run(instance, paused["id"], state_home=home, request_id=new_id())
    assert (
        control.inspect_run(instance, paused["id"], state_home=home)["status"]
        == "cancelled"
    )


def test_input_loop_consumes_one_stopped_failed_attempt(single_flow):
    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["inputs"] = ["loop"]
    workflow.write_text(command_graph_json(graph))
    (project / "loop").symlink_to("loop")
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    paused = start_run(instance, state_home=home)
    assert (
        paused["count"] == 1
        and paused["completions"][0]["transport"] == "execution_failure"
    )
    assert paused["completions"][0]["writer"] == "stopped"
    (project / "loop").unlink()
    (project / "loop").write_text("repaired")
    assert (
        control.inspect_run(instance, paused["id"], state_home=home)["completions"]
        == paused["completions"]
    )
    control.cancel_run(instance, paused["id"], state_home=home, request_id=new_id())
    assert (
        control.inspect_run(instance, paused["id"], state_home=home)["status"]
        == "cancelled"
    )


@pytest.mark.parametrize("use", ["ref", "input"])
def test_resolved_uppercase_owner_alias_is_rejected(single_flow, monkeypatch, use):
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    owner = project / ".millrace-router-owner"
    owner.mkdir()
    upper = project / ".MILLRACE-ROUTER-OWNER"
    if not upper.exists() or not upper.samefile(owner):
        pytest.skip("case-sensitive filesystem")
    alias = project / "ordinary"
    alias.symlink_to(upper.name)
    if use == "input":
        graph = json.loads(workflow.read_text())
        graph["planes"]["main"]["nodes"]["draft"]["invoke"]["inputs"] = [
            "ordinary/owner.json"
        ]
        workflow.write_text(command_graph_json(graph))
    calls = []

    def invoke(*args):
        calls.append(True)
        return CommandResult(
            b'{"outcome":"READY","refs":["ordinary/owner.json"]}',
            b"",
            "completed",
            "stopped",
            exit_code=0,
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    paused = start_run(instance, state_home=home)
    expected = "protocol_failure" if use == "ref" else "execution_failure"
    assert paused["completions"][0]["classification"] == expected
    assert len(calls) == (1 if use == "ref" else 0)
    alias.unlink()
    alias.mkdir()
    (alias / "owner.json").write_text("ordinary public content")
    assert (
        control.inspect_run(instance, paused["id"], state_home=home)["completions"]
        == paused["completions"]
    )
    control.cancel_run(instance, paused["id"], state_home=home, request_id=new_id())
    final = control.inspect_run(instance, paused["id"], state_home=home)
    assert final["status"] == "cancelled"
    assert control.inspect_run(instance, paused["id"], state_home=home) == final
