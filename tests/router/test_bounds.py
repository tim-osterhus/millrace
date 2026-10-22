import json
import time
from pathlib import Path

import pytest
from tests.router.support import (
    command_graph_json,
    create_instance,
    recover_run,
    start_run,
)

from millrace.router.store import Store


class Crash(BaseException):
    pass


@pytest.mark.parametrize("route", ["finish", "goto"])
def test_bounds_survive_retries_edits_and_restart(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    data["limits"]["max_elapsed_seconds"] = 2.0
    if route == "goto":
        data["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {"goto": "draft"}
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}
    original = Store.complete

    def after_result(store: Store, invocation: str, result: object) -> None:
        original(store, invocation, result)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", after_result)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    time.sleep(2.05)
    result = recover_run(instance, str(ids["run_id"]), state_home=home)
    assert result["count"] == 1
    assert len(result["transitions"]) == 1
    assert result["status"] == ("succeeded" if route == "finish" else "paused")
    if route == "goto":
        assert result["pause_reason"] == "limit_exhausted"
    assert recover_run(instance, str(ids["run_id"]), state_home=home) == result


@pytest.mark.parametrize("failure", ["input", "spawn", "run_timeout", "node_timeout"])
def test_failed_attempts_and_timeout_rules(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    node = data["planes"]["main"]["nodes"]["draft"]
    node["on_error"] = {"retry": {"limit": 1, "exhausted": {"finish": "failed"}}}
    if failure == "input":
        node["invoke"]["inputs"] = ["missing.txt"]
    if "timeout" in failure:
        node["invoke"].update(
            args=["wait"], timeout_seconds=0.1 if failure == "node_timeout" else 4
        )
        data["limits"]["max_elapsed_seconds"] = 0.15 if failure == "run_timeout" else 10
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    if failure == "spawn":

        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("fixture spawn refusal")

        monkeypatch.setattr("millrace.router.adapters.command.subprocess.Popen", fail)
    result = start_run(instance, state_home=home)
    assert result["count"] == (1 if failure == "run_timeout" else 2)
    assert result["status"] == ("paused" if failure == "run_timeout" else "failed")
    assert result["retry_counts"]["main"] == (
        {} if failure == "run_timeout" else {"draft": 1}
    )
    if failure == "run_timeout":
        assert result["pause_reason"] == "limit_exhausted"
    if failure in {"input", "spawn"}:
        assert all(i["pid"] is None for i in result["invocations"])
    assert recover_run(instance, str(result["id"]), state_home=home) == result


def test_retry_budget_survives_node_revisit(flow: tuple[Path, Path, Path]) -> None:
    workflow, project, home = flow
    (workflow.parent / "scripts/draft.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "p=Path('attempts');n=int(p.read_text())+1 if p.exists() else 1\n"
        "p.write_text(str(n))\nif n in (1,3):sys.exit(1)\n"
        'print(\'{"outcome":"READY"}\')\n'
    )
    (workflow.parent / "scripts/check.py").write_text(
        'print(\'{"outcome":"REVISE"}\')\n'
    )
    data = json.loads(workflow.read_text())
    data["planes"]["main"]["nodes"]["draft"]["on_error"] = {
        "retry": {"limit": 1, "exhausted": {"finish": "failed"}}
    }
    data["planes"]["main"]["nodes"]["check"]["invoke"]["inputs"] = []
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["count"] == 4
    assert result["status"] == "failed"
    assert result["retry_counts"]["main"] == {"draft": 1}


def test_attempt_cap_stops_before_another_reservation(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    data["limits"]["max_invocations"] = 2
    node = data["planes"]["main"]["nodes"]["draft"]
    node["invoke"]["inputs"] = ["still-missing.txt"]
    node["on_error"] = {"retry": {"limit": 10, "exhausted": {"finish": "failed"}}}
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == "paused"
    assert result["pause_reason"] == "limit_exhausted"
    assert result["count"] == 2
    assert result["retry_counts"]["main"] == {"draft": 2}
    assert len(result["invocations"]) == 2
    assert recover_run(instance, str(result["id"]), state_home=home) == result


def test_deadline_crossing_before_intent_does_not_consume_attempt(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    data["limits"]["max_elapsed_seconds"] = 0.1
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    reserve = Store.reserve

    def delayed(store: Store, *args: object) -> dict[str, object]:
        time.sleep(0.15)
        return reserve(store, *args)

    monkeypatch.setattr(Store, "reserve", delayed)
    result = start_run(instance, state_home=home)
    assert result["status"] == "paused"
    assert result["pause_reason"] == "limit_exhausted"
    assert result["count"] == 0
    assert result["invocations"] == []
    assert recover_run(instance, str(result["id"]), state_home=home) == result


def test_active_bounds_lower_and_restore_within_startup(
    single_flow: tuple[Path, Path, Path],
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
    first = start_run(instance, state_home=home, edit_mode="live")
    run = str(first["id"])

    def apply(limit: int, elapsed: float) -> dict[str, object]:
        stopped = control.pause_run(instance, run, state_home=home, request_id=new_id())
        assert stopped["status"] == "applied"
        graph["limits"] = {"max_invocations": limit, "max_elapsed_seconds": elapsed}
        workflow.write_text(command_graph_json(graph))
        current = control.inspect_run(instance, run, state_home=home)
        receipt = control.submit_revision(
            instance,
            run,
            state_home=home,
            request_id=new_id(),
            expected_state_version=current["state_version"],
        )
        pending = control.inspect_run(instance, run, state_home=home)
        assert receipt["status"] == "applied"
        accepted = control.accept_revision(
            instance,
            run,
            candidate_id=pending["pending_revision"]["candidate_id"],
            state_home=home,
            request_id=new_id(),
            expected_state_version=pending["state_version"],
        )
        assert accepted["status"] == "applied", accepted
        return control.inspect_run(instance, run, state_home=home)

    lowered = apply(2, 5)
    assert (
        lowered["count"] == 1 and lowered["planes"]["main"]["pause_reason"] == "review"
    )
    assert lowered["cap"] == 2 and lowered["deadline"] == first["started_at"] + 5
    resumed = control.resume_plane(
        instance,
        run,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
        expected_state_version=lowered["state_version"],
    )
    assert resumed["status"] == "applied"
    capped = control.inspect_run(instance, run, state_home=home)
    assert capped["count"] == 2
    refused = control.resume_plane(
        instance,
        run,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
        expected_state_version=capped["state_version"],
    )
    assert refused["code"] == "limit_exhausted"
    assert control.inspect_run(instance, run, state_home=home)["count"] == 2
    restored = apply(100, 1000)
    assert (
        restored["count"] == 2
        and restored["planes"]["main"]["pause_reason"] == "review"
    )
    assert restored["cap"] == first["startup_cap"]
    assert restored["deadline"] == first["startup_deadline"]
    assert (
        restored["started_at"] == first["started_at"]
        and restored["mode"] == first["mode"]
    )
    continued = control.resume_plane(
        instance,
        run,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
        expected_state_version=restored["state_version"],
    )
    assert continued["status"] == "applied"
    assert recover_run(instance, run, state_home=home)["count"] == 3


def test_retry_count_survives_removed_and_reintroduced_node(
    flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "check"}
    }
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {
        "retry": {"limit": 1, "exhausted": {"finish": "failed"}}
    }
    graph["planes"]["main"]["nodes"]["check"]["invoke"]["inputs"] = []
    graph["planes"]["main"]["nodes"]["check"]["on"]["ACCEPT"] = {
        "pause": {"reason": "checkpoint", "resume": "check"}
    }
    workflow.write_text(command_graph_json(graph))
    calls = 0

    def invoke(argv: list[str], *args: object) -> CommandResult:
        nonlocal calls
        if argv[1].endswith("draft.py"):
            calls += 1
            if calls in {1, 3}:
                return CommandResult(
                    b"", b"", "execution_failure", "stopped", exit_code=1
                )
            outcome = "READY"
        else:
            outcome = "ACCEPT"
        return CommandResult(
            command_graph_json({"outcome": outcome}).encode(),
            b"",
            "completed",
            "stopped",
            exit_code=0,
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    initial = start_run(instance, state_home=home, edit_mode="live")
    run = str(initial["id"])
    assert initial["retry_counts"]["main"] == {"draft": 1} and initial["count"] == 2
    removed = json.loads(command_graph_json(graph))
    removed["planes"]["main"]["start"] = "check"
    removed["planes"]["main"]["nodes"] = {
        "check": removed["planes"]["main"]["nodes"]["check"]
    }
    removed["planes"]["main"]["nodes"]["check"]["on"]["REVISE"] = {"goto": "check"}
    for selected in (removed, graph):
        stopped = control.pause_run(instance, run, state_home=home, request_id=new_id())
        assert stopped["status"] == "applied"
        workflow.write_text(command_graph_json(selected))
        before = control.inspect_run(instance, run, state_home=home)
        control.submit_revision(
            instance,
            run,
            state_home=home,
            request_id=new_id(),
            expected_state_version=before["state_version"],
        )
        pending = control.inspect_run(instance, run, state_home=home)
        accepted = control.accept_revision(
            instance,
            run,
            candidate_id=pending["pending_revision"]["candidate_id"],
            state_home=home,
            request_id=new_id(),
            expected_state_version=pending["state_version"],
        )
        assert accepted["status"] == "applied", accepted
        current = control.inspect_run(instance, run, state_home=home)
        assert current["count"] == 2 and current["planes"]["main"]["status"] == "paused"
        assert control.inspect_run(instance, run, state_home=home)["retry_counts"][
            "main"
        ] == {"draft": 1}
    before = control.inspect_run(instance, run, state_home=home)
    control.retry_invocation(
        instance,
        run,
        invocation_id=initial["invocations"][0]["id"],
        reason="retry after restore",
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
        plane_id="main",
    )
    final = control.inspect_run(instance, run, state_home=home)
    assert (
        final["status"] == "failed"
        and final["count"] == 3
        and final["retry_counts"]["main"] == {"draft": 2}
    )


def test_backward_clock_history_remains_loadable(single_flow, monkeypatch):
    import time
    from types import SimpleNamespace

    from millrace.router import control
    from millrace.router import store as store_module
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    paused = start_run(instance, state_home=home)
    real = time.time
    with monkeypatch.context() as patch:
        patch.setattr(store_module, "time", SimpleNamespace(time=lambda: real() - 2))
        control.resume_plane(
            instance,
            str(paused["id"]),
            state_home=home,
            request_id=new_id(),
            expected_state_version=paused["state_version"],
            plane_id="main",
        )
    after = control.inspect_run(instance, str(paused["id"]), state_home=home)
    assert after["count"] == 2
    assert control.inspect_run(instance, str(paused["id"]), state_home=home) == after
    control.cancel_run(
        instance, str(paused["id"]), state_home=home, request_id=new_id()
    )
    assert (
        control.inspect_run(instance, str(paused["id"]), state_home=home)["status"]
        == "cancelled"
    )


def test_expired_reconciled_failure_preserves_retry_budget(single_flow, monkeypatch):
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["limits"]["max_elapsed_seconds"] = 0.12
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = 10
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {
        "retry": {"limit": 2, "exhausted": {"finish": "failed"}}
    }
    workflow.write_text(command_graph_json(graph))
    monkeypatch.setattr(
        "millrace.router.engine.invoke",
        lambda *args: CommandResult(
            b"failed", b"", "execution_failure", "may_live", exit_code=1
        ),
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = before["id"]
    control.reconcile_invocation(
        instance,
        run,
        invocation_id=before["invocations"][0]["id"],
        reason="verified stopped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
        plane_id="main",
    )
    reconciled = control.inspect_run(instance, run, state_home=home)
    time.sleep(0.13)
    control.resume_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=reconciled["state_version"],
    )
    after = control.inspect_run(instance, run, state_home=home)
    assert (
        after["pause_reason"] == "limit_exhausted"
        and after["retry_counts"]["main"] == {}
    )
    assert after["transitions"] == [] and after["count"] == 1
    assert after["completions"] == reconciled["completions"]
    assert control.inspect_run(instance, run, state_home=home) == after
