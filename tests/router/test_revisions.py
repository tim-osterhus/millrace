import json
from pathlib import Path

import pytest
from tests.router.support import (
    command_graph_json,
    create_instance,
    inspect_run,
    start_run,
)

from millrace.router.store import Store


@pytest.mark.parametrize("edit", ["graph", "script", "helper", "context"])
def test_boundary_modes_and_incompatible_edits(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, edit: str
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    data["control_files"] = ["helper.txt"]
    (workflow.parent / "helper.txt").write_text("old")
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    original = Store.complete

    def complete(store: Store, invocation: str, result: object) -> None:
        original(store, invocation, result)
        if edit == "graph":
            changed = json.loads(workflow.read_text())
            changed["planes"]["main"]["nodes"]["draft"]["outcomes"] = ["OTHER"]
            changed["planes"]["main"]["nodes"]["draft"]["on"] = {
                "OTHER": {"finish": "failed"}
            }
            workflow.write_text(command_graph_json(changed))
        elif edit == "script":
            (workflow.parent / "scripts/draft.py").write_text(
                'print(\'{"outcome":"OTHER"}\')'
            )
        elif edit == "helper":
            (workflow.parent / "helper.txt").write_text("new")
        else:
            (project / "request.txt").write_text("ordinary context changed")

    monkeypatch.setattr(Store, "complete", complete)
    result = start_run(instance, state_home=home)
    assert result["count"] == 1
    assert result["completions"][0]["classification"] == "semantic"
    assert result["completions"][0]["outcome"] == "READY"
    assert result["mode"] == "locked"
    if edit == "context":
        assert result["status"] == "succeeded"
    else:
        assert result["status"] == "paused"
        assert result["pause_reason"] == "locked_edit"
        assert result["transitions"] == []
    assert inspect_run(instance, str(result["id"]), state_home=home) == result


def test_captured_script_wins_after_reservation(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    original = Store.reserve

    def reserve(
        store: Store, run: str, revision: object, plane_id: str
    ) -> dict[str, object]:
        assert plane_id == "main"
        result = original(store, run, revision, plane_id)
        (workflow.parent / "scripts/draft.py").write_text(
            "raise RuntimeError('new script must not execute')"
        )
        return result

    monkeypatch.setattr(Store, "reserve", reserve)
    result = start_run(instance, state_home=home)
    assert result["status"] == "paused"
    assert result["completions"][0]["outcome"] == "READY"
    assert result["completions"][0]["transport"] == "completed"


@pytest.mark.parametrize("edit", ["script", "binding", "helper"])
def test_locked_edit_before_first_reservation_launches_nothing(
    single_flow: tuple[Path, Path, Path], edit: str
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    data["control_files"] = ["helper.txt"]
    (workflow.parent / "helper.txt").write_text("old")
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])

    def changed(ids: dict[str, object]) -> None:
        if edit == "script":
            (workflow.parent / "scripts/draft.py").write_text("raise AssertionError()")
        elif edit == "helper":
            (workflow.parent / "helper.txt").write_text("new")
        else:
            data["planes"]["main"]["nodes"]["draft"]["invoke"]["args"] = ["different"]
            workflow.write_text(command_graph_json(data))

    result = start_run(instance, state_home=home, on_started=changed)
    assert result["status"] == "paused"
    assert result["pause_reason"] == "locked_edit"
    assert result["count"] == 0
    assert result["invocations"] == result["completions"] == []


@pytest.mark.parametrize(
    "change", ["missing_outcome", "missing_node", "invalid", "unknown_stays_unknown"]
)
def test_live_classification_uses_producing_contract(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    from millrace.router.records import CommandResult

    workflow, project, home = single_flow
    original = workflow.read_bytes()
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])

    def completed(*args: object) -> CommandResult:
        graph = json.loads(original)
        node = graph["planes"]["main"]["nodes"]["draft"]
        outcome = "OTHER" if change == "unknown_stays_unknown" else "READY"
        if change in {"missing_outcome", "unknown_stays_unknown"}:
            node["outcomes"] = ["OTHER"]
            node["on"] = {"OTHER": {"finish": "failed"}}
        elif change == "missing_node":
            graph["planes"]["main"]["nodes"] = {"replacement": node}
            graph["planes"]["main"]["start"] = "replacement"
            node["on_error"] = {"finish": "failed"}
            node["on_unknown"] = {"finish": "failed"}
        else:
            node["on"]["READY"] = {"goto": "absent"}
        workflow.write_text(command_graph_json(graph))
        return CommandResult(
            command_graph_json({"outcome": outcome}).encode(),
            b"",
            "completed",
            "stopped",
            exit_code=0,
        )

    monkeypatch.setattr("millrace.router.engine.invoke", completed)
    result = start_run(instance, state_home=home, edit_mode="live")
    assert result["count"] == 1 and result["status"] == "paused"
    assert result["completions"][0]["classification"] == (
        "unknown" if change == "unknown_stays_unknown" else "semantic"
    )
    assert (result["revision_id"] == result["startup_revision_id"]) is (
        change != "unknown_stays_unknown"
    )
    assert result["pause_reason"] == (
        "invalid_revision"
        if change == "invalid"
        else "no_runnable_planes"
        if change == "unknown_stays_unknown"
        else "incompatible_revision"
    )
    if change == "unknown_stays_unknown":
        assert result["planes"]["main"]["status"] == "paused"
        assert result["planes"]["main"]["pause_reason"] == "unknown_outcome"
        assert result["run_gate"] is None
    else:
        assert result["run_gate"]["reason"] == result["pause_reason"]
        assert result["transitions"] == []
    assert inspect_run(instance, str(result["id"]), state_home=home) == result


def test_folder_pending_supersession_and_unstable_capture(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    original = workflow.read_bytes()
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}

    def completed(*args: object) -> CommandResult:
        args[5](12345)
        b = json.loads(original)
        b["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {"finish": "failed"}
        workflow.write_text(command_graph_json(b))
        run = str(ids["run_id"])
        queued = control.submit_revision(
            instance,
            run,
            state_home=home,
            request_id=new_id(),
            expected_state_version=control.inspect_run(instance, run, state_home=home)[
                "state_version"
            ],
        )
        assert queued["status"] == "queued"
        args[4]()
        pending = control.inspect_run(instance, run, state_home=home)
        assert (
            pending["pending_revision"] is not None
            and pending["revision_id"] == pending["startup_revision_id"]
        )
        c = json.loads(original)
        c["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
            "pause": {"reason": "review", "resume": "draft"}
        }
        workflow.write_text(command_graph_json(c))
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    with monkeypatch.context() as patch:
        patch.setattr("millrace.router.engine.invoke", completed)
        result = start_run(
            instance, state_home=home, edit_mode="live", on_started=ids.update
        )
    assert result["count"] == 1 and result["pause_reason"] == "no_runnable_planes"
    assert result["planes"]["main"]["pause_reason"] == "review"
    assert result["run_gate"] is None
    assert [candidate["seq"] for candidate in result["candidates"]] == [1, 2]
    assert result["candidates"][1]["supersedes"] == 1
    assert (
        result["invocations"][0]["revision_id"]
        != result["transitions"][0]["revision_id"]
        == result["revision_id"]
    )
    run = str(result["id"])
    unchanged = control.submit_revision(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=result["state_version"],
    )
    assert unchanged["action"] == "unchanged"
    assert (
        control.inspect_run(instance, run, state_home=home)["state_version"]
        == result["state_version"]
    )
    stable = workflow.read_bytes()
    different = stable + b"\n"
    reads = 0
    import millrace.router.graph as graph_module

    actual_read = graph_module._control_bytes

    def unstable(path: Path) -> bytes:
        nonlocal reads
        if path == workflow:
            reads += 1
            return stable if reads % 2 else different
        return actual_read(path)

    with monkeypatch.context() as patch:
        patch.setattr(graph_module, "_control_bytes", unstable)
        diagnostic = control.validate_revision(instance, state_home=home)
        assert diagnostic["diagnostics"] == ["unstable_revision"] and reads == 6
    assert (
        control.inspect_run(instance, run, state_home=home)["state_version"]
        == result["state_version"]
    )
    with monkeypatch.context() as patch:
        patch.setattr(graph_module, "_control_bytes", unstable)
        control.resume_plane(
            instance,
            run,
            plane_id="main",
            state_home=home,
            request_id=new_id(),
            expected_state_version=result["state_version"],
        )
    paused = control.inspect_run(instance, run, state_home=home)
    assert paused["pause_reason"] == "unstable_revision" and paused["count"] == 1


def test_locked_restore_and_capture_only_acceptance(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    a = workflow.read_bytes()
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])

    def edit(*args: object) -> CommandResult:
        b = json.loads(a)
        b["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {"finish": "failed"}
        workflow.write_text(command_graph_json(b))
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    with monkeypatch.context() as patch:
        patch.setattr("millrace.router.engine.invoke", edit)
        before = start_run(instance, state_home=home)
    run = str(before["id"])
    assert before["pause_reason"] == "locked_edit" and before["transitions"] == []
    b_id = before["pending_revision"]["candidate_id"]
    c = json.loads(a)
    c["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(c))
    request_id = new_id()
    captured = control.accept_revision(
        instance,
        run,
        candidate_id=b_id,
        state_home=home,
        request_id=request_id,
        expected_state_version=before["state_version"],
    )
    assert (
        captured["status"] == "applied"
        and captured["action"] == "candidate_superseded"
        and captured["accepted_revision"] is False
    )
    after = control.inspect_run(instance, run, state_home=home)
    assert after["state_version"] == before["state_version"] + 1
    assert (
        after["revision_id"] == before["revision_id"]
        and after["transitions"] == []
        and after["count"] == 1
    )
    assert (
        control.accept_revision(
            instance,
            run,
            candidate_id=b_id,
            state_home=home,
            request_id=request_id,
            expected_state_version=before["state_version"],
        )
        == captured
    )
    wrong = control.accept_revision(
        instance,
        run,
        candidate_id=b_id,
        state_home=home,
        request_id=new_id(),
        expected_state_version=after["state_version"],
    )
    assert wrong["code"] == "stale_candidate"
    assert (
        control.inspect_run(instance, run, state_home=home)["state_version"]
        == after["state_version"]
    )
    workflow.write_bytes(a)
    restored = control.submit_revision(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=after["state_version"],
    )
    assert restored["status"] == "applied"
    restored_view = control.inspect_run(instance, run, state_home=home)
    assert (
        restored_view["pending_revision"] is None
        and restored_view["candidates"][-1]["withdrawn"] is True
    )
    control.resume_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=restored_view["state_version"],
    )
    final = control.inspect_run(instance, run, state_home=home)
    assert (
        final["status"] == "succeeded"
        and final["count"] == 1
        and final["mode"] == "locked"
    )
    assert (
        final["invocations"][0]["revision_id"] == final["transitions"][0]["revision_id"]
    )


def test_edit_after_committed_edge_does_not_reroute(
    flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import millrace.router.engine as engine

    workflow, project, home = flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    original = engine._boundary
    changed = False

    def after(store: Store, run: str) -> None:
        nonlocal changed
        original(store, run)
        committed = store.db.execute(
            "SELECT COUNT(*) FROM transitions WHERE run_id=?", (run,)
        ).fetchone()[0]
        if committed and not changed:
            changed = True
            graph = json.loads(workflow.read_text())
            graph["planes"]["main"]["nodes"] = {
                "draft": graph["planes"]["main"]["nodes"]["draft"]
            }
            graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {"goto": "draft"}
            workflow.write_text(command_graph_json(graph))

    monkeypatch.setattr(engine, "_boundary", after)
    result = start_run(instance, state_home=home, edit_mode="live")
    assert (
        result["status"] == "paused"
        and result["pause_reason"] == "incompatible_revision"
    )
    assert result["count"] == 1 and len(result["transitions"]) == 1
    assert result["transitions"][0]["target"] == "check"
    assert result["revision_id"] == result["transitions"][0]["revision_id"]
    assert result["pending_revision"]["candidate_id"] != result["revision_id"]


def test_locked_acceptance_remains_locked_on_next_edit(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    script = workflow.parent / "scripts/draft.py"
    original_script = script.read_bytes()

    def changed(*args: object) -> CommandResult:
        graph = json.loads(workflow.read_text())
        graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
            "pause": {"reason": "review", "resume": "draft"}
        }
        workflow.write_text(command_graph_json(graph))
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    with monkeypatch.context() as patch:
        patch.setattr("millrace.router.engine.invoke", changed)
        before = start_run(instance, state_home=home)
    run = str(before["id"])
    accepted = control.accept_revision(
        instance,
        run,
        candidate_id=before["pending_revision"]["candidate_id"],
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
    )
    assert accepted["accepted_revision"] is True
    current = control.inspect_run(instance, run, state_home=home)
    assert (
        current["mode"] == "locked"
        and current["pause_reason"] == "no_runnable_planes"
        and current["planes"]["main"]["pause_reason"] == "review"
        and current["run_gate"] is None
        and current["count"] == 1
    )
    assert (
        current["transitions"][0]["revision_id"]
        != current["invocations"][0]["revision_id"]
    )
    script.write_text('raise RuntimeError("must not launch")\n')
    control.resume_plane(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=current["state_version"],
        plane_id="main",
    )
    edited = control.inspect_run(instance, run, state_home=home)
    assert edited["pause_reason"] == "locked_edit" and edited["count"] == 1
    assert edited["transitions"] == current["transitions"]
    script.write_bytes(original_script)
    withdrawn = control.submit_revision(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=edited["state_version"],
    )
    assert withdrawn["status"] == "applied", withdrawn
    restored = control.inspect_run(instance, run, state_home=home)
    assert restored["pending_revision"] is None
    assert restored["candidates"][-1]["withdrawn"] is True
    assert restored["run_gate"] == edited["run_gate"] and restored["count"] == 1
    control.resume_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=restored["state_version"],
    )
    after = control.inspect_run(instance, run, state_home=home)
    assert (
        after["mode"] == "locked"
        and after["count"] == 2
        and after["pause_reason"] == "no_runnable_planes"
        and after["planes"]["main"]["pause_reason"] == "review"
        and after["run_gate"] is None
    )


def test_redirect_retained_incompatible_completion_keeps_raw_outcome(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    nodes = graph["planes"]["main"]["nodes"]
    nodes["replacement"] = json.loads(command_graph_json(nodes["draft"]))
    nodes["replacement"]["on_error"] = {"finish": "failed"}
    nodes["replacement"]["on_unknown"] = {"finish": "failed"}
    nodes["draft"]["on_error"] = {"goto": "replacement"}
    workflow.write_text(command_graph_json(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])

    def completed(*args: object) -> CommandResult:
        graph = json.loads(workflow.read_text())
        replacement = graph["planes"]["main"]["nodes"]["replacement"]
        graph["planes"]["main"]["nodes"], graph["planes"]["main"]["start"] = (
            {"replacement": replacement},
            "replacement",
        )
        workflow.write_text(command_graph_json(graph))
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    with monkeypatch.context() as patch:
        patch.setattr("millrace.router.engine.invoke", completed)
        before = start_run(instance, state_home=home, edit_mode="live")
    run = str(before["id"])
    assert before["pause_reason"] == "incompatible_revision"
    assert before["revision_id"] == before["startup_revision_id"]
    control.pause_plane(
        instance, run, plane_id="main", state_home=home, request_id=new_id()
    )
    paused = control.inspect_run(instance, run, state_home=home)
    redirected = control.redirect_plane(
        instance,
        run,
        node_id="replacement",
        reason="explicit replacement",
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
        plane_id="main",
    )
    assert redirected["status"] == "applied", redirected
    moved = control.inspect_run(instance, run, state_home=home)
    assert moved["run_gate"] == before["run_gate"]
    assert moved["count"] == 1 and moved["planes"]["main"]["cursor"] == "replacement"
    accepted = control.accept_revision(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=moved["state_version"],
        candidate_id=moved["pending_revision"]["candidate_id"],
    )
    assert accepted["status"] == "applied" and accepted["accepted_revision"] is True
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "succeeded" and final["count"] == 2
    assert final["completions"][0] == before["completions"][0]
    assert len(final["transitions"]) == 1 and len(final["operator_moves"]) == 1
    assert final["operator_moves"][0]["completion_id"] == before["invocations"][0]["id"]


def test_manual_retry_launches_current_revision_contract(
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
    before = start_run(instance, state_home=home, edit_mode="live")
    graph["planes"]["main"]["nodes"]["draft"]["outcomes"] = ["OTHER"]
    graph["planes"]["main"]["nodes"]["draft"]["on"] = {"OTHER": {"finish": "succeeded"}}
    (workflow.parent / "scripts/draft.py").write_text(
        'print(\'{"outcome":"OTHER"}\')\n'
    )
    workflow.write_text(command_graph_json(graph))
    control.retry_invocation(
        instance,
        str(before["id"]),
        invocation_id=before["invocations"][0]["id"],
        reason="new contract",
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
        plane_id="main",
    )
    final = control.inspect_run(instance, str(before["id"]), state_home=home)
    assert (
        final["status"] == "succeeded"
        and final["count"] == 2
        and final["retry_counts"]["main"] == {"draft": 1}
    )
    assert (
        final["completions"][0]["outcome"] == "READY"
        and final["completions"][1]["outcome"] == "OTHER"
    )
    assert (
        final["invocations"][0]["revision_id"] != final["invocations"][1]["revision_id"]
    )
