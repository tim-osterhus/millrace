import json
import sys
from pathlib import Path

import pytest
from tests.router.support import command_graph_json

from millrace.router.graph import capture, validate_workflow
from millrace.router.records import RouterError

_EMPTY_CONTEXT = {"state_version": 1, "runtime_defaults": {}, "operator_policy": {}}


def _context_executable(flow: tuple[Path, Path, Path]) -> tuple[str, Path]:
    workflow, _, home = flow
    alias = "p3a_context_python_4ecfe920"
    graph = json.loads(workflow.read_text())
    graph["bindings"]["python"]["executable"] = alias
    workflow.write_text(command_graph_json(graph))
    selected = home.parent / "selected-bin"
    selected.mkdir()
    (selected / alias).symlink_to(sys.executable)
    return alias, selected


@pytest.mark.parametrize("selection", ["path", "host_paths"])
def test_runnable_capture_honors_selected_inputs(single_flow, selection):
    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    alias, selected = _context_executable(single_flow)
    created = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        path=(str(selected),) if selection == "path" else (),
        host_paths={alias: sys.executable} if selection == "host_paths" else {},
    )
    assert created["status"] == "applied"
    context = control.load_instance(str(created["instance_id"]), home).settings()
    assert validate_workflow(workflow, project, settings_context=context)


def test_runnable_capture_cannot_borrow_excluded_ambient_path(single_flow, monkeypatch):
    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    _, selected = _context_executable(single_flow)
    excluded = home.parent / "excluded-bin"
    excluded.mkdir()
    monkeypatch.setenv("PATH", str(selected))
    created = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        path=(str(excluded),),
    )
    assert created["status"] == "refused" and created["code"] == "missing_executable"
    with pytest.raises(RouterError) as refused:
        validate_workflow(
            workflow,
            project,
            settings_context={
                **_EMPTY_CONTEXT,
                "path": [str(excluded)],
                "host_paths": {},
            },
        )
    assert refused.value.code == "missing_executable"


def test_authored_graph_and_prerequisite_parity(flow: tuple[Path, Path, Path]) -> None:
    workflow, project, _ = flow
    assert not (project / "out/brief.txt").exists()
    assert validate_workflow(workflow, project, settings_context=_EMPTY_CONTEXT) is True
    original = workflow.read_text()
    mutations = [
        lambda d: d.update(extra=True),
        lambda d: d["limits"].update(max_invocations=True),
        lambda d: d["limits"].update(max_elapsed_seconds=float("inf")),
        lambda d: d["planes"]["main"]["nodes"]["draft"]["on"].update(
            READY={"goto": "absent"}
        ),
        lambda d: d["planes"]["main"]["nodes"]["draft"].update(
            outcomes=["READY", "READY"]
        ),
        lambda d: d["planes"]["main"]["nodes"]["draft"]["on_error"]["retry"].update(
            exhausted={"retry": {"limit": 0, "exhausted": {"finish": "failed"}}}
        ),
        lambda d: d["planes"]["main"]["nodes"]["draft"]["invoke"].update(
            script="../outside.py"
        ),
        lambda d: d["planes"]["main"]["nodes"]["draft"]["invoke"].update(
            inputs=[".millrace-router-owner/owner.json"]
        ),
        lambda d: d["bindings"]["python"].update(adapter="unknown_adapter"),
    ]
    for mutate in mutations:
        data = json.loads(original)
        mutate(data)
        workflow.write_text(command_graph_json(data))
        with pytest.raises(RouterError):
            validate_workflow(workflow, project, settings_context=_EMPTY_CONTEXT)
    workflow.write_text(
        original.replace('"start": "draft"', '"start": "draft", "start": "draft"')
    )
    with pytest.raises(RouterError, match="start"):
        validate_workflow(workflow, project, settings_context=_EMPTY_CONTEXT)
    workflow.write_text(original)
    script = workflow.parent / "scripts/draft.py"
    script.unlink()
    with pytest.raises(RouterError) as missing:
        validate_workflow(workflow, project, settings_context=_EMPTY_CONTEXT)
    assert missing.value.code == "missing_control"


def test_coherent_snapshot_and_bounded_unstable_capture(
    flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, _ = flow
    helper = workflow.parent / "helper.txt"
    helper.write_text("first")
    data = json.loads(workflow.read_text())
    data["control_files"] = ["helper.txt"]
    workflow.write_text(command_graph_json(data))
    a = capture(workflow, project)
    helper.write_text("second")
    b = capture(workflow, project)
    assert a.revision_id != b.revision_id
    assert a.files["helper.txt"] == b"first"
    import millrace.router.graph as graph_module

    original_read = graph_module._control_bytes
    reads = 0

    def changing(path: Path) -> bytes:
        nonlocal reads
        if path == helper:
            reads += 1
            return str(reads).encode()
        return original_read(path)

    monkeypatch.setattr(graph_module, "_control_bytes", changing)
    with pytest.raises(RouterError) as unstable:
        capture(workflow, project)
    assert unstable.value.code == "unstable_revision"
    assert reads == 6


def test_large_integer_refusal_and_empty_literal_argument(
    flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, _ = flow
    original = workflow.read_text()
    for field in ("invocations", "retry"):
        data = json.loads(original)
        if field == "invocations":
            data["limits"]["max_invocations"] = 10**400
        else:
            data["planes"]["main"]["nodes"]["draft"]["on_error"]["retry"]["limit"] = (
                10**400
            )
        workflow.write_text(command_graph_json(data))
        with pytest.raises(RouterError) as error:
            capture(workflow, project)
        assert error.value.code == "invalid_number"
    data = json.loads(original)
    data["planes"]["main"]["nodes"]["draft"]["invoke"]["args"] = [""]
    workflow.write_text(command_graph_json(data))
    assert capture(workflow, project).graph.planes["main"].nodes["draft"].config[
        "args"
    ] == [""]


def test_empty_control_persist_reload_parity(flow: tuple[Path, Path, Path]) -> None:
    from tests.router.support import create_instance, inspect_run, start_run

    workflow, project, home = flow
    data = json.loads(workflow.read_text())
    data["control_files"] = ["empty.txt"]
    (workflow.parent / "empty.txt").write_bytes(b"")
    workflow.write_text(command_graph_json(data))
    assert validate_workflow(workflow, project, settings_context=_EMPTY_CONTEXT)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == "succeeded"
    assert inspect_run(instance, str(result["id"]), state_home=home) == result


def test_empty_listed_control_survives_pending_activation(
    single_flow: tuple[Path, Path, Path],
) -> None:
    from tests.router.support import create_instance, start_run

    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["control_files"] = ["settings.json"]
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    (workflow.parent / "settings.json").write_bytes(b"")
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    assert before["planes"]["main"]["pause_reason"] == "review"
    control.pause_run(instance, str(before["id"]), state_home=home, request_id=new_id())
    before = control.inspect_run(instance, str(before["id"]), state_home=home)
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {"finish": "succeeded"}
    workflow.write_text(command_graph_json(graph))
    control.submit_revision(
        instance,
        str(before["id"]),
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
    )
    pending = control.inspect_run(instance, str(before["id"]), state_home=home)
    assert pending["pending_revision"]["files"]["settings.json"] == ""
    control.accept_revision(
        instance,
        str(before["id"]),
        candidate_id=pending["pending_revision"]["candidate_id"],
        state_home=home,
        request_id=new_id(),
        expected_state_version=pending["state_version"],
    )
    accepted = control.inspect_run(instance, str(before["id"]), state_home=home)
    assert accepted["status"] == "paused" and accepted["run_gate"] is None
    assert accepted["planes"]["main"]["pause_reason"] == "review"
    control.resume_plane(
        instance,
        str(before["id"]),
        plane_id="main",
        state_home=home,
        request_id=new_id(),
        expected_state_version=accepted["state_version"],
    )
    final = control.inspect_run(instance, str(before["id"]), state_home=home)
    assert final["status"] == "succeeded" and final["count"] == 2


def test_user_authored_stage_branch_and_captured_instruction_files(
    single_flow: tuple[Path, Path, Path],
) -> None:
    from tests.router.support import create_instance, start_run

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["control_files"] = ["prompts/policy.txt", "settings/runtime.json"]
    (workflow.parent / "prompts").mkdir()
    (workflow.parent / "settings").mkdir()
    (workflow.parent / "prompts/policy.txt").write_text("approve bounded text")
    (workflow.parent / "settings/runtime.json").write_text("{}")
    (workflow.parent / "scripts/draft.py").write_text(
        "from pathlib import Path\n"
        'Path("draft.txt").write_text("bounded text")\n'
        'print(\'{"outcome":"READY","refs":["draft.txt"]}\')\n'
    )
    (workflow.parent / "scripts/approve.py").write_text(
        "from pathlib import Path\n"
        "root=Path(__file__).resolve().parents[1]\n"
        'assert (root/"prompts/policy.txt").read_text()=="approve bounded text"\n'
        'assert (root/"settings/runtime.json").read_text()=="{}"\n'
        'assert Path("draft.txt").read_text()=="bounded text"\n'
        'print(\'{"outcome":"ACCEPT"}\')\n'
    )
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {"goto": "approval"}
    graph["planes"]["main"]["nodes"]["approval"] = {
        "invoke": {
            "binding": "python",
            "script": "scripts/approve.py",
            "timeout_seconds": 1,
            "inputs": ["draft.txt"],
        },
        "result": "json-v1",
        "outcomes": ["ACCEPT", "REVISE"],
        "on": {"ACCEPT": {"finish": "succeeded"}, "REVISE": {"goto": "draft"}},
        "on_error": {"finish": "failed"},
        "on_unknown": {"finish": "failed"},
    }
    workflow.write_text(command_graph_json(graph))
    assert not (project / "draft.txt").exists()
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    final = start_run(instance, state_home=home)
    assert final["status"] == "succeeded" and [
        inv["node"] for inv in final["invocations"]
    ] == ["draft", "approval"]
    assert (project / "draft.txt").read_text() == "bounded text"


def test_invalid_route_diagnostic_names_authored_field(
    single_flow: tuple[Path, Path, Path],
) -> None:
    from millrace.router.graph import parse_graph
    from millrace.router.records import RouterError

    workflow, _, _ = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"] = {}
    with pytest.raises(RouterError) as refusal:
        parse_graph(command_graph_json(graph).encode())
    assert refusal.value.code == "invalid_routes"
    assert "planes.main.nodes.draft.on" in str(refusal.value)


@pytest.mark.parametrize("target", ["diagnose", "draft"])
def test_retry_exhaustion_goto_preserves_bounds(single_flow, monkeypatch, target):
    import sqlite3

    from tests.router.support import create_instance

    from millrace.router import control, engine
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["limits"]["max_invocations"] = 3
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "retry": {"limit": 1, "exhausted": {"goto": target}}
    }
    if target == "diagnose":
        graph["planes"]["main"]["nodes"][target] = json.loads(
            command_graph_json(graph["planes"]["main"]["nodes"]["draft"])
        )
        graph["planes"]["main"]["nodes"][target]["on"]["READY"] = {
            "finish": "succeeded"
        }
    workflow.write_text(command_graph_json(graph))
    monkeypatch.setattr(
        engine,
        "invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        ),
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    boundary = engine._boundary

    class Crash(BaseException):
        pass

    def committed(store, run):
        result = boundary(store, run)
        if store.db.execute(
            "SELECT COUNT(*) FROM transitions WHERE run_id=?", (run,)
        ).fetchone()[0]:
            raise Crash()
        return result

    started = []
    with monkeypatch.context() as patch:
        patch.setattr(engine, "_boundary", committed)
        with pytest.raises(Crash):
            control.start_run(
                instance,
                state_home=home,
                request_id=new_id(),
                expected_state_version=1,
                on_started=started.append,
            )
    run = started[0]["run_id"]
    before = control.inspect_run(instance, run, state_home=home)
    assert before["count"] == 1 and len(before["transitions"]) == 1
    assert before["retry_counts"]["main"] == {"draft": 1}
    original = {
        key: before[key]
        for key in ("started_at", "startup_cap", "startup_elapsed", "startup_deadline")
    }
    control.recover_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
    )
    final = control.inspect_run(instance, run, state_home=home)
    assert final["count"] == 3 and final["retry_counts"]["main"] == {"draft": 1}
    assert {key: final[key] for key in original} == original
    assert json.loads(final["transitions"][1]["action"]) == {"goto": target}
    assert final["transitions"][1]["retry_delta"] == 0
    assert final["invocations"][2]["node"] == target
    if target == "draft":
        assert final["status"] == "paused"
        assert final["pause_reason"] == "limit_exhausted"
    else:
        assert final["status"] == "succeeded"
    assert control.inspect_run(instance, run, state_home=home) == final
    database = control.load_instance(instance, home).store_path
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE retries SET count=2 WHERE run_id=?", (run,))
    with pytest.raises(RouterError) as corrupt:
        control.inspect_run(instance, run, state_home=home)
    assert corrupt.value.code == "counter_integrity"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE retries SET count=1 WHERE run_id=?", (run,))
    assert control.inspect_run(instance, run, state_home=home) == final
    if target == "draft":
        control.cancel_run(instance, run, state_home=home, request_id=new_id())
        final = control.inspect_run(instance, run, state_home=home)
        assert final["status"] == "cancelled" and final["count"] == 3
    assert not (project / ".millrace-router-owner/owner.json").exists()
    assert control.inspect_run(instance, run, state_home=home) == final


@pytest.mark.parametrize("kind", ["control", "workflow", "cwd"])
def test_symlink_loop_is_a_diagnostic(flow, kind):
    from millrace.router.graph import capture, capture_candidate
    from millrace.router.records import RouterError

    workflow, project, _ = flow
    if kind == "workflow":
        workflow.unlink()
        workflow.symlink_to(workflow.name)
    elif kind == "control":
        script = workflow.parent / "scripts/draft.py"
        script.unlink()
        script.symlink_to(script.name)
    else:
        graph = json.loads(workflow.read_text())
        graph["planes"]["main"]["nodes"]["draft"]["invoke"]["cwd"] = "loop"
        workflow.write_text(command_graph_json(graph))
        (project / "loop").symlink_to("loop")
    for _ in range(2):
        try:
            result = capture_candidate(
                workflow,
                project,
                search_path=(str(Path(sys.executable).parent),),
                host_paths={},
            )
            if kind == "cwd":
                assert result.diagnostic is None and result.revision is not None
                with pytest.raises(RouterError) as refusal:
                    capture(
                        workflow,
                        project,
                        search_path=(str(Path(sys.executable).parent),),
                        host_paths={},
                    )
                assert refusal.value.code == "invalid_path"
            else:
                assert result.diagnostic == "invalid_path"
        except RouterError as exc:
            assert exc.code == "invalid_path"


@pytest.mark.parametrize("name", ["workflow.json", "scripts/draft.py"])
def test_nonregular_capture_is_bounded_and_matches_validator(flow, name):
    import os
    import signal

    from millrace.router.graph import capture, capture_candidate
    from millrace.router.records import RouterError

    workflow, project, _ = flow
    target = workflow.parent / name
    original = target.read_bytes()
    target.unlink()
    os.mkfifo(target)

    def timed_out(*args):
        raise AssertionError("nonregular capture blocked")

    old = signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL, 1)
    try:
        candidate = capture_candidate(
            workflow,
            project,
            search_path=(str(Path(sys.executable).parent),),
            host_paths={},
        )
        assert candidate.diagnostic == "missing_control"
        with pytest.raises(RouterError) as refusal:
            capture(workflow, project)
        assert refusal.value.code == "missing_control"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
        target.unlink()
        target.write_bytes(original)
    assert capture(workflow, project)


@pytest.mark.parametrize(
    "op", ["create", "validate", "submit", "accept", "inspect", "boundary"]
)
def test_fifo_control_all_entrypoints_are_bounded(single_flow, monkeypatch, op):
    import os
    import signal

    from tests.router.support import create_instance, start_run

    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["control_files"] = ["policy.txt"]
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    policy = workflow.parent / "policy.txt"
    policy.write_text("a")
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    before = start_run(instance, state_home=home)
    run = before["id"]
    pending = None
    if op == "accept":
        pause_request = new_id()
        control.pause_run(instance, run, state_home=home, request_id=pause_request)
        before = control.inspect_run(instance, run, state_home=home)
        assert before["run_gate"] == {
            "reason": "operator_pause",
            "request_id": pause_request,
        }
        assert before["planes"]["main"]["pause_reason"] == "review"
        policy.write_text("b")
        control.submit_revision(
            instance,
            run,
            state_home=home,
            request_id=new_id(),
            expected_state_version=before["state_version"],
        )
        pending = control.inspect_run(instance, run, state_home=home)

    def fifo():
        policy.unlink()
        os.mkfifo(policy)

    if op != "boundary":
        fifo()

    def timeout(*args):
        raise AssertionError("FIFO entrypoint blocked")

    previous = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 1)
    try:
        if op == "create":
            receipt = control.create_instance(
                workflow,
                project,
                state_home=home,
                request_id=new_id(),
                expected_state_version=0,
            )
            assert receipt["code"] == "missing_control"
        elif op == "validate":
            assert control.validate_revision(instance, state_home=home)[
                "diagnostics"
            ] == ["missing_control"]
        elif op == "submit":
            assert (
                control.submit_revision(
                    instance,
                    run,
                    state_home=home,
                    request_id=new_id(),
                    expected_state_version=before["state_version"],
                )["status"]
                == "applied"
            )
        elif op == "accept":
            receipt = control.accept_revision(
                instance,
                run,
                candidate_id=pending["pending_revision"]["candidate_id"],
                state_home=home,
                request_id=new_id(),
                expected_state_version=pending["state_version"],
            )
            assert receipt["accepted_revision"] is False
        elif op == "inspect":
            assert (
                control.inspect_run(instance, run, state_home=home)["draft_revision"][
                    "diagnostic"
                ]
                == "missing_control"
            )
        else:

            def invoke(*args):
                fifo()
                return CommandResult(
                    b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
                )

            monkeypatch.setattr("millrace.router.engine.invoke", invoke)
            control.resume_plane(
                instance,
                run,
                plane_id="main",
                state_home=home,
                request_id=new_id(),
                expected_state_version=before["state_version"],
            )
            assert (
                control.inspect_run(instance, run, state_home=home)["pause_reason"]
                == "invalid_revision"
            )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if policy.exists():
            policy.unlink()
        policy.write_text("a")
    assert control.validate_revision(instance, state_home=home)["valid"]
    final = control.inspect_run(instance, run, state_home=home)
    assert control.inspect_run(instance, run, state_home=home) == final
