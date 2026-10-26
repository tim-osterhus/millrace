from pathlib import Path

import pytest
from tests.router.support import (
    command_graph_json,
    create_instance,
    inspect_run,
    recover_run,
    start_run,
)

from millrace.router.store import Store


class Crash(BaseException):
    pass


@pytest.mark.parametrize("point", ["before_intent", "after_intent"])
def test_launch_gap_never_blindly_replays(
    flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    workflow, project, home = flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}
    original = Store.reserve

    def crash(store: Store, *args: object) -> dict[str, object]:
        if point == "after_intent":
            original(store, *args)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "reserve", crash)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    result = recover_run(instance, str(ids["run_id"]), state_home=home)
    if point == "after_intent":
        assert result["status"] == "paused"
        assert result["pause_reason"] == "writer_unknown"
        assert result["count"] == 1
        assert not (project / "out/brief.txt").exists()
        assert result["completions"] == []
    else:
        assert result["status"] == "succeeded"
        assert result["count"] == 2
    assert recover_run(instance, str(ids["run_id"]), state_home=home) == result
    assert inspect_run(instance, str(ids["run_id"]), state_home=home) == result


@pytest.mark.parametrize("optional", [False, True])
def test_unconfirmed_completion_blocks_error_retry(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    optional: bool,
) -> None:
    import json

    from millrace.router.records import CommandResult

    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    data["planes"]["main"]["nodes"]["draft"]["on_error"] = {
        "retry": {"limit": 2, "exhausted": {"finish": "failed"}}
    }
    if optional:
        from tests.router.support import optional_graph_json

        workflow.write_text(optional_graph_json(data, {}))
    else:
        workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    # Adapter-fact fixture: valid-looking output cannot prove a writer stopped.
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY"}', b"", "timeout", "may_live", "cleanup_unconfirmed"
        ),
    )
    result = start_run(instance, state_home=home)
    assert result["status"] == "paused"
    assert result["pause_reason"] == "writer_unknown"
    assert result["count"] == 1
    assert result["completions"][0]["classification"] == "timeout"
    assert result["transitions"] == []
    assert result["retry_counts"]["main"] == {}
    assert (project / ".millrace-router-owner/owner.json").exists()
    assert recover_run(instance, str(result["id"]), state_home=home) == result


@pytest.mark.parametrize(
    "transport",
    ["completed", "execution_failure", "protocol_failure", "timeout", "run_deadline"],
)
def test_reconciliation_preserves_retained_completion(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
) -> None:
    import json
    import time

    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {
        "retry": {"limit": 1, "exhausted": {"finish": "failed"}}
    }
    if transport == "run_deadline":
        graph["limits"]["max_elapsed_seconds"] = 0.15
        graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = 10
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    calls = 0
    raw = b'{"outcome":"READY"}'

    def invoke(*args: object) -> CommandResult:
        nonlocal calls
        calls += 1
        if calls > 1:
            return CommandResult(raw, b"", "completed", "stopped", exit_code=0)
        return CommandResult(
            raw,
            b"",
            "timeout" if transport == "run_deadline" else transport,
            "may_live",
            "unconfirmed cleanup",
            0 if transport == "completed" else 1,
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    before = start_run(instance, state_home=home)
    run = str(before["id"])
    original = before["completions"][0]
    assert before["status"] == "paused" and before["transitions"] == []
    request_id = new_id()
    receipt = control.reconcile_invocation(
        instance,
        run,
        invocation_id=before["invocations"][0]["id"],
        stopped=True,
        reason="verified stopped",
        state_home=home,
        request_id=request_id,
        expected_state_version=before["state_version"],
        plane_id="main",
    )
    assert receipt["status"] == "applied"
    reconciled = control.inspect_run(instance, run, state_home=home)
    assert reconciled["state_version"] == before["state_version"] + 1
    assert reconciled["count"] == 1 and reconciled["transitions"] == []
    assert {
        key: value
        for key, value in reconciled["completions"][0].items()
        if key != "effective_writer"
    } == {key: value for key, value in original.items() if key != "effective_writer"}
    assert reconciled["completions"][0]["effective_writer"] == "stopped"
    assert (
        reconciled["invocations"][0]["ref_admission"]
        == before["invocations"][0]["ref_admission"]
    )
    assert (
        control.reconcile_invocation(
            instance,
            run,
            invocation_id=before["invocations"][0]["id"],
            stopped=True,
            reason="verified stopped",
            state_home=home,
            request_id=request_id,
            expected_state_version=before["state_version"],
            plane_id="main",
        )
        == receipt
    )
    if transport == "run_deadline":
        time.sleep(0.16)
    control.resume_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=reconciled["state_version"],
    )
    final = control.inspect_run(instance, run, state_home=home)
    if transport == "run_deadline":
        assert (
            final["status"] == "paused"
            and final["pause_reason"] == "limit_exhausted"
            and final["count"] == 1
            and final["retry_counts"]["main"] == {}
        )
    else:
        assert final["status"] == "succeeded" and final["count"] == (
            1 if transport == "completed" else 2
        )
        assert final["retry_counts"]["main"] == (
            {} if transport == "completed" else {"draft": 1}
        )
    assert final["completions"][0]["classification"] == original["classification"]
    assert final["completions"][0]["stdout_base64"] == original["stdout_base64"]
    assert control.inspect_run(instance, run, state_home=home) == final


def test_interrupted_intent_reconciliation_creates_only_failure(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {
        "retry": {"limit": 1, "exhausted": {"finish": "failed"}}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}
    reserve = Store.reserve

    def crash(store: Store, *args: object) -> None:
        reserve(store, *args)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "reserve", crash)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    paused = recover_run(instance, str(ids["run_id"]), state_home=home)
    assert paused["completions"] == [] and paused["pause_reason"] == "writer_unknown"
    run = str(paused["id"])
    control.reconcile_invocation(
        instance,
        run,
        invocation_id=paused["invocations"][0]["id"],
        reason="verified no writer",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
        plane_id="main",
    )
    reconciled = control.inspect_run(instance, run, state_home=home)
    assert reconciled["count"] == 1 and reconciled["transitions"] == []
    assert (
        reconciled["completions"][0]["transport"] == "interrupted"
        and reconciled["completions"][0]["stdout"] == ""
    )
    control.resume_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=reconciled["state_version"],
    )
    final = control.inspect_run(instance, run, state_home=home)
    assert (
        final["status"] == "succeeded"
        and final["count"] == 2
        and final["retry_counts"]["main"] == {"draft": 1}
    )
    assert final["completions"][0]["classification"] == "interrupted"


def test_active_reconciliation_is_refused_before_interruption(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}
    request_id = new_id()

    def invoke(*args: object) -> CommandResult:
        args[5](12345)
        active = control.inspect_run(instance, str(ids["run_id"]), state_home=home)
        queued = control.reconcile_invocation(
            instance,
            str(ids["run_id"]),
            invocation_id=active["invocations"][0]["id"],
            reason="too early",
            stopped=True,
            state_home=home,
            request_id=request_id,
            expected_state_version=active["state_version"],
            plane_id="main",
        )
        assert queued["status"] == "queued"
        args[4]()
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "may_live", exit_code=0
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    paused = start_run(instance, state_home=home, on_started=ids.update)
    current = control.inspect_run(instance, str(paused["id"]), state_home=home)
    receipt = next(
        row for row in current["controls"] if row["request_id"] == request_id
    )
    assert (
        receipt["status"] == "refused"
        and receipt["code"] == "invocation_not_reconcilable"
    )
    assert current["writer_facts"] == [] and current["pause_reason"] == "writer_unknown"



@pytest.mark.parametrize("route", ["finish", "pause"])
def test_optional_bounds_result_first_and_fresh_recover_no_dispatch(
    single_flow, monkeypatch, route
):
    import json
    import os
    import subprocess
    import sys

    from tests.router.support import optional_graph_json

    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    node = graph["planes"]["main"]["nodes"]["draft"]
    node["on"]["READY"] = (
        {"finish": "succeeded"} if route == "finish"
        else {"pause": {"reason": "review", "resume": "draft"}}
    )
    (workflow.parent / "scripts/draft.py").write_text(
        "from pathlib import Path\np=Path('dispatch-count')\n"
        "p.write_text(str(int(p.read_text())+1 if p.exists() else 1))\n"
        'print(\'{"outcome":"READY"}\')\n'
    )
    workflow.write_text(optional_graph_json(graph, {}))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids = {}
    original = Store.complete

    def after_result(store, *args):
        original(store, *args)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", after_result)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    run = str(ids["run_id"])
    raw = inspect_run(instance, run, state_home=home)
    assert raw["count"] == 1 and len(raw["completions"]) == 1
    assert raw["transitions"] == []

    def forbidden(*args, **kwargs):
        raise AssertionError("fresh recovery dispatched domain code")

    monkeypatch.setattr("millrace.router.engine.invoke", forbidden)
    recovered = recover_run(instance, run, state_home=home)
    assert recovered["status"] == ("succeeded" if route == "finish" else "paused")
    assert recovered["count"] == 1 and len(recovered["transitions"]) == 1
    assert recovered["completions"][0]["stdout"] == raw["completions"][0]["stdout"]
    # A new request, not a replayed receipt, must also avoid domain dispatch.
    receipt = control.recover_run(
        instance, run, state_home=home, request_id=new_id(),
        expected_state_version=recovered["state_version"],
    )
    assert receipt["status"] == "applied"
    assert inspect_run(instance, run, state_home=home) == recovered
    # A newly imported controller process must observe terminal/quiescent state
    # without executing the deterministic domain script again.
    program = (
        "import json,sys; from pathlib import Path; "
        "from millrace.router import control; "
        "r=control.recover_run(sys.argv[1],sys.argv[2],"
        "state_home=Path(sys.argv[3]),request_id=sys.argv[4],"
        "expected_state_version=int(sys.argv[5])); print(json.dumps(r))"
    )
    child = subprocess.run(
        [sys.executable, "-c", program, instance, run, str(home), new_id(),
         str(recovered["state_version"])],
        cwd=project, capture_output=True, text=True, timeout=15,
        env={"PATH": os.defpath,
             "PYTHONPATH": str(Path(control.__file__).resolve().parents[2]),
             "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert child.returncode == 0, child.stdout + child.stderr
    assert json.loads(child.stdout)["status"] == "applied"
    assert (project / "dispatch-count").read_text() == "1"
    assert inspect_run(instance, run, state_home=home) == recovered
