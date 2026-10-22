import json
import shutil
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

import millrace.router.engine as engine
from millrace.router.records import RouterError
from millrace.router.store import Store, file_lock


class Crash(BaseException):
    pass


@pytest.mark.parametrize(
    "point", ["before_claim", "after_claim", "running", "terminal"]
)
def test_persistent_hold_and_writer_aftermath(
    flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    workflow, project, home = flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}
    claim = engine.claim_hold
    state = Store.set_state
    boundary = engine._boundary

    def claim_crash(*args: object, **kwargs: object) -> None:
        if point == "before_claim":
            raise Crash()
        claim(*args, **kwargs)
        raise Crash()

    def state_crash(
        store: Store, run: str, status: str, reason: str | None = None, **kwargs
    ) -> None:
        state(store, run, status, reason, **kwargs)
        if status == "running":
            raise Crash()

    def route_crash(store: Store, run: str, completion_id: str | None = None) -> bool:
        result = boundary(store, run)
        if store.run(run)["status"] == "succeeded":
            raise Crash()
        return result

    with monkeypatch.context() as patch:
        if "claim" in point:
            patch.setattr(engine, "claim_hold", claim_crash)
        elif point == "running":
            patch.setattr(Store, "set_state", state_crash)
        else:
            patch.setattr(engine, "_boundary", route_crash)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    owner = project / ".millrace-router-owner/owner.json"
    assert owner.exists() is (point != "before_claim")
    competitor_folder = workflow.parent.parent / "competitor"
    shutil.copytree(workflow.parent, competitor_folder)
    other = str(
        create_instance(competitor_folder / "workflow.json", project, state_home=home)[
            "instance_id"
        ]
    )
    other_ids: dict[str, object] = {}
    if point == "before_claim":
        competing = start_run(other, state_home=home, on_started=other_ids.update)
        assert competing["status"] == "succeeded"
    else:
        with pytest.raises(RouterError) as conflict:
            start_run(other, state_home=home, on_started=other_ids.update)
        assert conflict.value.code == "project_owned"
        assert (
            inspect_run(other, str(other_ids["run_id"]), state_home=home)["count"] == 0
        )
    result = recover_run(instance, str(ids["run_id"]), state_home=home)
    assert result["status"] == "succeeded"
    assert result["count"] == 2
    assert not owner.exists()
    assert recover_run(instance, str(ids["run_id"]), state_home=home) == result
    assert (
        recover_run(other, str(other_ids["run_id"]), state_home=home)["status"]
        == "succeeded"
    )
    assert not owner.exists()


def test_competitor_and_wrong_hold_never_replace_writer(
    flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    ids: dict[str, object] = {}
    original = Store.complete

    def crash(store: Store, *args: object) -> None:
        original(store, *args)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", crash)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    second_workflow = workflow.parent.parent / "other"
    shutil.copytree(workflow.parent, second_workflow)
    other = str(
        create_instance(second_workflow / "workflow.json", project, state_home=home)[
            "instance_id"
        ]
    )
    other_ids: dict[str, object] = {}
    with pytest.raises(RouterError) as refusal:
        start_run(other, state_home=home, on_started=other_ids.update)
    assert refusal.value.code == "project_owned"
    assert inspect_run(other, str(other_ids["run_id"]), state_home=home)["count"] == 0
    owner = project / ".millrace-router-owner/owner.json"
    saved = owner.read_bytes()
    data = json.loads(saved)
    data["hold_id"] = "0" * 32
    owner.write_text(command_graph_json(data))
    with pytest.raises(RouterError):
        recover_run(instance, str(ids["run_id"]), state_home=home)
    owner.write_bytes(saved)  # isolated corrupt fixture restoration, no live user state
    assert (
        recover_run(instance, str(ids["run_id"]), state_home=home)["status"]
        == "succeeded"
    )
    assert (
        recover_run(other, str(other_ids["run_id"]), state_home=home)["status"]
        == "succeeded"
    )
    assert not owner.exists()
    config = load_instance(instance, home)
    with file_lock(config.state_dir / ".controller.lock"):
        with pytest.raises(RouterError) as locked:
            start_run(instance, state_home=home)
    assert locked.value.code == "busy"


@pytest.mark.parametrize("protected", [True, False])
def test_input_owner_alias_is_rechecked_at_dispatch(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    protected: bool,
) -> None:
    from millrace.router.records import CommandResult

    workflow, project, home = single_flow
    ordinary = project / "ordinary"
    ordinary.mkdir()
    (ordinary / "owner.json").write_text("ordinary input")
    alias = project / "alias"
    alias.symlink_to("ordinary", target_is_directory=True)
    data = json.loads(workflow.read_text())
    data["planes"]["main"]["nodes"]["draft"]["invoke"]["inputs"] = ["alias/owner.json"]
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    reserve = Store.reserve
    calls: list[object] = []

    def reserved(store: Store, *args: object) -> dict[str, object]:
        invocation = reserve(store, *args)
        if protected:
            alias.unlink()
            alias.symlink_to(".millrace-router-owner", target_is_directory=True)
        return invocation

    def command(*args: object) -> CommandResult:
        calls.append(args)
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr(Store, "reserve", reserved)
    monkeypatch.setattr(engine, "invoke", command)
    result = start_run(instance, state_home=home)
    assert result["count"] == 1
    assert len(calls) == (0 if protected else 1)
    completion = result["completions"][0]
    if protected:
        assert result["status"] == "paused"
        assert result["pause_reason"] == "no_runnable_planes"
        assert result["planes"]["main"]["pause_reason"] == "execution_error"
        assert completion["transport"] == "execution_failure"
        assert completion["diagnostic"] == "invalid_path"
        assert completion["writer"] == "stopped"
    else:
        assert result["status"] == "succeeded"
        assert completion["classification"] == "semantic"
    assert recover_run(instance, str(result["id"]), state_home=home) == result


def test_orphan_receipt_corruption_cannot_release_nonterminal_owner(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    monkeypatch.setattr(
        engine,
        "invoke",
        lambda *args: CommandResult(b"", b"", "execution_failure", "unknown"),
    )
    paused = start_run(instance, state_home=home)
    request_id = new_id()
    kwargs = {
        "project_root": project,
        "hold_id": paused["hold_id"],
        "owner_instance_id": instance,
        "owner_run_id": paused["id"],
        "reason": "verified",
        "stopped": True,
        "request_id": request_id,
        "state_home": home,
    }
    receipt = control.release_project_hold(**kwargs)
    assert receipt["code"] == "owner_run_nonterminal"
    path = home / "orphan-receipts" / (request_id + ".json")
    value = json.loads(path.read_text())
    value["receipt"]["status"] = "applied"
    path.write_text(command_graph_json(value))
    with pytest.raises(RouterError):
        control.release_project_hold(**kwargs)
    assert (project / ".millrace-router-owner/owner.json").exists()


def test_hold_claim_release_crash_windows_and_orphan_receipt(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    monkeypatch.setattr(
        engine,
        "invoke",
        lambda *args: CommandResult(b"", b"", "execution_failure", "unknown"),
    )
    paused = start_run(instance, state_home=home)
    owner = project / ".millrace-router-owner/owner.json"
    old_hold = owner.read_bytes()
    kwargs = {
        "project_root": project,
        "hold_id": paused["hold_id"],
        "owner_instance_id": instance,
        "owner_run_id": paused["id"],
        "reason": "verified stopped",
        "stopped": True,
        "state_home": home,
    }
    with file_lock(
        control.load_instance(instance, home).state_dir / ".controller.lock"
    ):
        normal = control.release_project_hold(**kwargs, request_id=new_id())
    assert normal["code"] == "owner_run_nonterminal" and normal["retryable"] is False
    assert (
        control.release_project_hold(**kwargs, request_id=new_id())["code"]
        == "owner_run_nonterminal"
    )
    config = control.load_instance(instance, home)
    saved = config.store_path.read_bytes()
    config.store_path.write_bytes(b"corrupt existing store")
    assert (
        control.release_project_hold(**kwargs, request_id=new_id())["code"]
        == "owner_store_unverifiable"
    )
    assert owner.read_bytes() == old_hold
    config.store_path.write_bytes(saved)
    config.store_path.unlink()
    request = new_id()

    def crash(path: Path) -> None:
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(control, "_unlink_hold", crash)
        with pytest.raises(Crash):
            control.release_project_hold(**kwargs, request_id=request)
    assert owner.read_bytes() == old_hold
    retained = json.loads((home / "orphan-receipts" / (request + ".json")).read_text())
    assert retained["receipt"]["status"] == "applied" and retained["receipt"][
        "original_hold"
    ] == json.loads(old_hold)
    receipt = control.release_project_hold(**kwargs, request_id=request)
    assert receipt == retained["receipt"] and not owner.exists()
    other_home = home.parent / "replacement-state"
    other_workflow = workflow.parent.parent / "replacement-flow"
    shutil.copytree(workflow.parent, other_workflow)
    replacement = str(
        create_instance(
            other_workflow / "workflow.json", project, state_home=other_home
        )["instance_id"]
    )
    other = start_run(replacement, state_home=other_home)
    new_hold = owner.read_bytes()
    assert control.release_project_hold(**kwargs, request_id=request) == receipt
    assert owner.read_bytes() == new_hold and new_hold != old_hold
    control.reconcile_invocation(
        replacement,
        str(other["id"]),
        invocation_id=other["invocations"][0]["id"],
        reason="test facts stopped",
        stopped=True,
        state_home=other_home,
        request_id=new_id(),
        expected_state_version=other["state_version"],
        plane_id="main",
    )
    control.cancel_run(
        replacement, str(other["id"]), state_home=other_home, request_id=new_id()
    )
    assert not owner.exists()


def test_orphan_final_receipt_precedes_changed_root_precondition(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    monkeypatch.setattr(
        engine,
        "invoke",
        lambda *args: CommandResult(b"", b"", "execution_failure", "unknown"),
    )
    paused = start_run(instance, state_home=home)
    request = new_id()
    first = control.release_project_hold(
        project_root=project,
        hold_id=paused["hold_id"],
        owner_instance_id=instance,
        owner_run_id=paused["id"],
        reason="verified",
        stopped=True,
        state_home=home,
        request_id=request,
    )
    retained = json.loads((home / "orphan-receipts" / (request + ".json")).read_text())[
        "request"
    ]
    moved = project.with_name("moved-project")
    project.rename(moved)
    project.symlink_to(moved, target_is_directory=True)
    assert control.execute_request(retained, state_home=home) == first
    assert (moved / ".millrace-router-owner/owner.json").exists()


def test_terminal_commit_crash_allows_fresh_same_instance_start(
    single_flow, monkeypatch
):
    from millrace.router import control, engine
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    monkeypatch.setattr(
        engine,
        "invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        ),
    )
    original = engine._boundary

    class Crash(BaseException):
        pass

    def crash(store, run, completion_id=None):
        answer = original(store, run)
        if store.run(run)["status"] == "succeeded":
            raise Crash()
        return answer

    ids = {}
    with monkeypatch.context() as patch:
        patch.setattr(engine, "_boundary", crash)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    assert (project / ".millrace-router-owner/owner.json").exists()
    second = start_run(instance, state_home=home)
    assert second["status"] == "succeeded" and second["count"] == 1
    old = control.inspect_run(instance, ids["run_id"], state_home=home)
    control.recover_run(
        instance,
        ids["run_id"],
        state_home=home,
        request_id=new_id(),
        expected_state_version=old["state_version"],
    )
    assert control.inspect_run(
        instance, ids["run_id"], state_home=home
    ) == control.inspect_run(instance, ids["run_id"], state_home=home)
    assert not (project / ".millrace-router-owner/owner.json").exists()


@pytest.mark.parametrize("kind", ["missing_run", "dangling_store", "denied_probe"])
def test_existing_unverifiable_orphan_never_releases(single_flow, monkeypatch, kind):
    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    config = control.load_instance(instance, home)
    run, hold, request = new_id(), new_id(), new_id()
    folder = project / ".millrace-router-owner"
    folder.mkdir()
    owner = {
        "hold_id": hold,
        "instance_id": instance,
        "run_id": run,
        "store_path": str(config.store_path),
    }
    (folder / "owner.json").write_text(command_graph_json(owner))
    if kind == "dangling_store":
        config.store_path.unlink()
        config.store_path.symlink_to("absent.sqlite3")
    elif kind == "denied_probe":
        original = Path.lstat

        def denied(path, *args, **kwargs):
            if path == config.store_path:
                raise PermissionError("fixture probe denied")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", denied)
    receipt = control.release_project_hold(
        project_root=project,
        owner_instance_id=instance,
        owner_run_id=run,
        hold_id=hold,
        reason="verified stopped",
        stopped=True,
        state_home=home,
        request_id=request,
    )
    assert (
        receipt["status"] == "refused" and receipt["code"] == "owner_store_unverifiable"
    )
    assert (
        control.release_project_hold(
            project_root=project,
            owner_instance_id=instance,
            owner_run_id=run,
            hold_id=hold,
            reason="verified stopped",
            stopped=True,
            state_home=home,
            request_id=request,
        )
        == receipt
    )
    assert json.loads((folder / "owner.json").read_text()) == owner


@pytest.mark.parametrize("point", ["unlink", "fsync"])
def test_orphan_cleanup_io_error_preserves_final_application(
    single_flow, monkeypatch, point
):
    import os

    from millrace.router import control
    from millrace.router.store import new_id

    _, project, home = single_flow
    home.mkdir()
    folder = project / ".millrace-router-owner"
    folder.mkdir()
    owner = folder / "owner.json"
    instance, run, hold, request = new_id(), new_id(), new_id(), new_id()
    old = dict(
        hold_id=hold,
        instance_id=instance,
        run_id=run,
        store_path=str(
            control.canonical_path(home) / "instances" / instance / "state.sqlite3"
        ),
    )
    owner.write_text(command_graph_json(old))
    kwargs = dict(
        project_root=project,
        hold_id=hold,
        owner_instance_id=instance,
        owner_run_id=run,
        reason="verified stopped",
        stopped=True,
        state_home=home,
        request_id=request,
    )
    original = control._unlink_hold
    writes = []
    atomic = control.atomic_json

    def observe(path, value):
        if path.parent.name == "orphan-receipts":
            writes.append(json.loads(command_graph_json(value)))
        return atomic(path, value)

    def failed(path):
        if point == "unlink":
            raise PermissionError("hold directory is read-only")

        def failed_fsync(descriptor):
            raise OSError("hold directory fsync failed after unlink")

        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", failed_fsync)
            original(path)

    with monkeypatch.context() as patch:
        patch.setattr(control, "atomic_json", observe)
        patch.setattr(control, "_unlink_hold", failed)
        with pytest.raises(OSError):
            control.release_project_hold(**kwargs)
        receipt_path = home / "orphan-receipts" / (request + ".json")
        saved = receipt_path.read_bytes()
        receipt = json.loads(saved)["receipt"]
        assert [item["receipt"]["status"] for item in writes] == ["applied"]
        assert receipt["status"] == "applied" and receipt["original_hold"] == old
        assert owner.exists() is (point == "unlink")
        if point == "unlink":
            with pytest.raises(OSError):
                control.release_project_hold(**kwargs)
        else:
            assert control.release_project_hold(**kwargs) == receipt
        assert receipt_path.read_bytes() == saved
    assert control.release_project_hold(**kwargs) == receipt
    assert control.release_project_hold(**kwargs) == receipt
    assert not owner.exists() and receipt_path.read_bytes() == saved
    replacement = {**old, "hold_id": new_id()}
    owner.write_text(command_graph_json(replacement))
    replacement_bytes = owner.read_bytes()
    assert control.release_project_hold(**kwargs) == receipt
    assert (
        owner.read_bytes() == replacement_bytes and receipt_path.read_bytes() == saved
    )
    with pytest.raises(RouterError, match="request_id_reused"):
        control.release_project_hold(**{**kwargs, "reason": "changed body"})
    assert owner.read_bytes() == replacement_bytes


@pytest.mark.parametrize(
    "kind",
    [
        "garbage",
        "unknown_run",
        "terminal_mismatch",
        "directory",
        "missing_run_id",
        "foreign",
    ],
)
def test_automatic_cleanup_never_strands_stop_receipts(single_flow, monkeypatch, kind):
    import sqlite3

    from millrace.router import control
    from millrace.router.ownership import claim_hold
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
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
    old = start_run(instance, state_home=home)
    control.cancel_run(instance, old["id"], state_home=home, request_id=new_id())
    control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    )
    before = control.inspect_instance(instance, state_home=home)["runs"][-1]
    run = before["id"]
    before = control.inspect_run(instance, run, state_home=home)
    owner = project / ".millrace-router-owner/owner.json"
    good = owner.read_bytes()
    record = json.loads(good)
    if kind == "garbage":
        owner.write_text("{garbage")
    elif kind == "unknown_run":
        owner.write_text(command_graph_json({**record, "run_id": new_id()}))
    elif kind == "terminal_mismatch":
        owner.write_text(
            command_graph_json({**record, "run_id": old["id"], "hold_id": new_id()})
        )
    elif kind == "directory":
        owner.unlink()
        owner.mkdir()
    elif kind == "missing_run_id":
        del record["run_id"]
        owner.write_text(command_graph_json(record))
    else:
        owner.write_text(command_graph_json({**record, "instance_id": new_id()}))
    damaged = None if owner.is_dir() else owner.read_bytes()
    pause = control.pause_run(instance, run, state_home=home, request_id=new_id())
    cancel = control.cancel_run(instance, run, state_home=home, request_id=new_id())
    assert pause["status"] == cancel["status"] == "applied"
    assert pause["action"] == "paused" and cancel["action"] == "cancelled"
    assert owner.is_dir() if damaged is None else owner.read_bytes() == damaged
    config = control.load_instance(instance, home)
    with pytest.raises((RouterError, OSError)):
        claim_hold(config, before)
    refused = control.release_project_hold(
        project_root=project,
        hold_id=before["hold_id"],
        owner_instance_id=instance,
        owner_run_id=run,
        reason="verified stopped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
    )
    assert refused["status"] == "refused"
    assert owner.is_dir() if damaged is None else owner.read_bytes() == damaged
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "cancelled" and final["count"] == 1
    assert final["completions"] == before["completions"]
    assert final == control.inspect_run(instance, run, state_home=home)
    with sqlite3.connect(config.store_path) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM controls WHERE status='queued'"
            ).fetchone()[0]
            == 0
        )
    assert (
        control.cancel_run(
            instance, run, state_home=home, request_id=cancel["request_id"]
        )
        == cancel
    )
    # Restore only this fixture, then exact own-terminal cleanup is safe.
    if owner.is_dir():
        owner.rmdir()
    owner.write_bytes(good)
    fresh = control.pause_run(instance, run, state_home=home, request_id=new_id())
    assert fresh["action"] == "already_terminal" and not owner.exists()
    assert (
        control.inspect_run(instance, run, state_home=home)["state_version"]
        == final["state_version"]
    )


def test_automatic_cleanup_locks_selection(single_flow, monkeypatch):
    import sqlite3
    import traceback

    from millrace.router import control, controller
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    monkeypatch.setattr(
        engine,
        "invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        ),
    )
    x = str(create_instance(workflow, project, state_home=home)["instance_id"])
    rx = start_run(x, state_home=home)["id"]
    control.cancel_run(x, rx, state_home=home, request_id=new_id())
    other = workflow.parent.with_name("workflow-y")
    shutil.copytree(workflow.parent, other)
    y = str(
        create_instance(other / workflow.name, project, state_home=home)["instance_id"]
    )
    ry = start_run(y, state_home=home)["id"]
    owner = project / ".millrace-router-owner/owner.json"
    original = controller._read_owner
    observed = {}

    def read(path, **kwargs):
        if (
            path == owner
            and not observed
            and any(
                frame.name == "release_terminal_hold"
                for frame in traceback.extract_stack()
            )
        ):
            try:
                with file_lock(owner.parent / "lock"):
                    pass
            except RouterError as exc:
                assert exc.code == "controller_busy"
                observed["locked"] = True
            else:
                observed["locked"] = False
                observed["cancel"] = control.cancel_run(
                    y, ry, state_home=home, request_id=new_id()
                )
        return original(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(controller, "_read_owner", read)
        receipt = control.pause_run(x, rx, state_home=home, request_id=new_id())
    assert observed["locked"]
    assert receipt["action"] == "already_terminal"
    assert json.loads(owner.read_bytes())["instance_id"] == y
    control.cancel_run(y, ry, state_home=home, request_id=new_id())
    assert not owner.exists()
    assert (
        control.pause_run(x, rx, state_home=home, request_id=receipt["request_id"])
        == receipt
    )
    assert control.inspect_run(x, rx, state_home=home)["status"] == "cancelled"
    assert control.inspect_run(y, ry, state_home=home)["status"] == "cancelled"
    with sqlite3.connect(control.load_instance(x, home).store_path) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM controls WHERE status='queued'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("change", ["vanished", "replacement"])
def test_automatic_cleanup_rechecks_selected_hold(single_flow, monkeypatch, change):
    from millrace.router import control, controller
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    monkeypatch.setattr(
        engine,
        "invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        ),
    )
    boundary = engine._boundary
    ids = {}

    def crash(store, run, completion_id=None):
        result = boundary(store, run)
        if store.run(run)["status"] == "succeeded":
            raise Crash()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(engine, "_boundary", crash)
        with pytest.raises(Crash):
            control.start_run(
                instance,
                state_home=home,
                request_id=new_id(),
                expected_state_version=1,
                on_started=ids.update,
            )
    owner = project / ".millrace-router-owner/owner.json"
    original = controller.release_hold
    changed = []

    def interleave(config, run):
        # Selection must release its descriptor before the existing helper locks.
        with file_lock(owner.parent / "lock"):
            pass
        if change == "vanished":
            owner.unlink()
            changed.append(None)
        else:
            replacement = {**json.loads(owner.read_bytes()), "hold_id": new_id()}
            owner.write_text(command_graph_json(replacement))
            changed.append(owner.read_bytes())
        original(config, run)

    with monkeypatch.context() as patch:
        patch.setattr(controller, "release_hold", interleave)
        receipt = control.pause_run(
            instance, ids["run_id"], state_home=home, request_id=new_id()
        )
    assert len(changed) == 1 and receipt["action"] == "already_terminal"
    assert (
        not owner.exists() if change == "vanished" else owner.read_bytes() == changed[0]
    )
    assert (
        control.pause_run(
            instance, ids["run_id"], state_home=home, request_id=receipt["request_id"]
        )
        == receipt
    )
    view = control.inspect_run(instance, ids["run_id"], state_home=home)
    assert view["status"] == "succeeded" and view["count"] == 1
    assert view == control.inspect_run(instance, ids["run_id"], state_home=home)


@pytest.mark.parametrize(
    "kind", ["folder_file", "lock_directory", "denied_mkdir", "denied_lock"]
)
def test_ready_claim_io_failures_finalize_stop_receipts(single_flow, monkeypatch, kind):
    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    folder = project / ".millrace-router-owner"
    if kind == "folder_file":
        folder.write_bytes(b"unchanged invalid owner folder")
    elif kind == "lock_directory":
        folder.mkdir()
        (folder / "lock").mkdir()
    original_mkdir, original_open = Path.mkdir, Path.open

    def mkdir(path, *args, **kwargs):
        if path == folder:
            raise PermissionError("fixture denied ownership mkdir")
        return original_mkdir(path, *args, **kwargs)

    def opened(path, *args, **kwargs):
        if path == folder / "lock":
            raise PermissionError("fixture denied ownership lock")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if kind == "denied_mkdir":
            patch.setattr(Path, "mkdir", mkdir)
        if kind == "denied_lock":
            patch.setattr(Path, "open", opened)
        ids = {}
        with pytest.raises((RouterError, OSError)):
            control.start_run(
                instance,
                state_home=home,
                request_id=new_id(),
                expected_state_version=1,
                on_started=ids.update,
            )
        run = ids["run_id"]
        before = control.inspect_run(instance, run, state_home=home)
        assert before["status"] == "ready" and before["count"] == 0
        request = new_id()
        refused = control.pause_run(instance, run, state_home=home, request_id=request)
        assert (
            refused["status"] == "refused"
            and refused["code"] == "owner_store_unverifiable"
        )
        assert refused["state_version"] == before["state_version"]
        cancelled = control.cancel_run(
            instance, run, state_home=home, request_id=new_id()
        )
        assert cancelled["status"] == "applied"
    if kind == "folder_file":
        assert folder.read_bytes() == b"unchanged invalid owner folder"
        folder.unlink()
    elif kind == "lock_directory":
        assert (folder / "lock").is_dir()
        (folder / "lock").rmdir()
    assert (
        control.pause_run(instance, run, state_home=home, request_id=request) == refused
    )
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "cancelled" and final["count"] == 0
    assert final["invocations"] == [] and final["completions"] == []
    assert all(row["status"] != "queued" for row in final["controls"])
    assert final == control.inspect_run(instance, run, state_home=home)
    monkeypatch.setattr(
        engine,
        "invoke",
        lambda *args: CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        ),
    )
    fresh = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    )
    assert (
        control.inspect_run(instance, fresh["run_id"], state_home=home)["status"]
        == "succeeded"
    )
    assert not (folder / "owner.json").exists()


def _ownership_probe(operation, instance, run, home, project, receipt=None):
    import subprocess
    import sys

    payload = dict(
        operation=operation,
        instance=instance,
        run=run,
        home=str(home),
        project=str(project),
        receipt=receipt,
    )
    code = """
import json,sys
from pathlib import Path
from millrace.router import control
from millrace.router.ownership import claim_hold,release_hold
from millrace.router.records import RouterError
from millrace.router.store import Store
p=json.loads(sys.argv[1]); home=Path(p['home'])
try:
 if p['operation'] in ('claim','release'):
  config=control.load_instance(p['instance'],home); store=Store(config,readonly=True)
  try:
   run=store.run(p['run'])
   if p['operation']=='claim': claim_hold(config,run,allow_missing=True)
   else: release_hold(config,run)
  finally: store.close()
  result={'status':'completed'}
 elif p['operation']=='auto':
  result=control.pause_run(p['instance'],p['run'],state_home=home,request_id=p['receipt']['request_id'])
 else:
  result=control.release_project_hold(state_home=home,project_root=Path(p['project']),**p['receipt'])
except RouterError as e: result={'status':'error','code':e.code}
except OSError as e: result={'status':'raw_os_error','code':type(e).__name__}
print(json.dumps(result))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, command_graph_json(payload)],
            capture_output=True,
            text=True,
            timeout=1.5,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            "owner read blocked beyond the owned child deadline"
        ) from exc
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "operation", ["auto", "claim", "release", "orphan_fresh", "orphan_replay"]
)
@pytest.mark.parametrize(
    "kind", ["fifo", "directory", "socket", "device", "oversized", "dangling"]
)
def test_owner_reader_rejects_nonregular_and_oversized(
    single_flow, monkeypatch, operation, kind
):
    import os
    import socket

    from millrace.router import control
    from millrace.router.records import CommandResult
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    folder = project / ".millrace-router-owner"
    owner = folder / "owner.json"
    receipt = None
    if operation.startswith("orphan"):
        home.mkdir()
        folder.mkdir()
        (folder / "lock").write_bytes(b"")
        instance, run, hold = new_id(), new_id(), new_id()
        original = dict(
            hold_id=hold,
            instance_id=instance,
            run_id=run,
            store_path=str(
                control.canonical_path(home) / "instances" / instance / "state.sqlite3"
            ),
        )
        good = command_graph_json(original).encode()
        owner.write_bytes(good)
        receipt = dict(
            hold_id=hold,
            owner_instance_id=instance,
            owner_run_id=run,
            reason="verified stopped",
            stopped=True,
            request_id=new_id(),
        )
        if operation == "orphan_replay":

            def crash(path):
                raise Crash()

            with monkeypatch.context() as patch:
                patch.setattr(control, "_unlink_hold", crash)
                with pytest.raises(Crash):
                    control.release_project_hold(
                        project_root=project, state_home=home, **receipt
                    )
            receipt_path = home / "orphan-receipts" / (receipt["request_id"] + ".json")
            applied_bytes = receipt_path.read_bytes()
    else:
        graph = json.loads(workflow.read_text())
        graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
            "pause": {"reason": "review", "resume": "draft"}
        }
        workflow.write_text(command_graph_json(graph))
        monkeypatch.setattr(
            engine,
            "invoke",
            lambda *args: CommandResult(
                b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
            ),
        )
        instance = str(
            create_instance(workflow, project, state_home=home)["instance_id"]
        )
        paused = start_run(instance, state_home=home)
        run = paused["id"]
        good = owner.read_bytes()
        if operation == "release":
            control.cancel_run(instance, run, state_home=home, request_id=new_id())
            owner.write_bytes(good)
        receipt = {"request_id": new_id()} if operation == "auto" else None
    owner.unlink()
    endpoint = None
    if kind == "fifo":
        os.mkfifo(owner)
    elif kind == "directory":
        owner.mkdir()
    elif kind == "socket":
        endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with monkeypatch.context() as patch:
            patch.chdir(folder)
            endpoint.bind("owner.json")
    elif kind == "device":
        owner.symlink_to("/dev/null")
    elif kind == "oversized":
        owner.write_bytes(good + b" " * 65537)
    else:
        owner.symlink_to(project / "absent-owner-target")
    try:
        result = _ownership_probe(operation, instance, run, home, project, receipt)
        if operation == "auto":
            assert result["status"] == "applied" and result["action"] == "paused"
        elif operation == "orphan_fresh":
            assert result["status"] == "refused"
        else:
            assert result["status"] == "error", result
        assert owner.is_symlink() or owner.exists()
        if kind == "oversized":
            assert owner.read_bytes() == good + b" " * 65537
        if operation == "orphan_replay":
            assert receipt_path.read_bytes() == applied_bytes
    finally:
        if endpoint is not None:
            endpoint.close()
        if owner.is_dir():
            owner.rmdir()
        else:
            owner.unlink(missing_ok=True)
    owner.write_bytes(good)
    if operation == "orphan_fresh":
        assert (
            _ownership_probe(operation, instance, run, home, project, receipt) == result
        )
        receipt = {**receipt, "request_id": new_id()}
    repaired = _ownership_probe(operation, instance, run, home, project, receipt)
    assert repaired["status"] in {"applied", "completed"}
    if operation in {"auto", "claim"}:
        control.cancel_run(instance, run, state_home=home, request_id=new_id())
        view = control.inspect_run(instance, run, state_home=home)
        assert view["status"] == "cancelled"
        assert view == control.inspect_run(instance, run, state_home=home)
    if operation == "orphan_replay":
        assert receipt_path.read_bytes() == applied_bytes
        assert (
            _ownership_probe(operation, instance, run, home, project, receipt)
            == repaired
        )
    assert not owner.exists()


def test_owner_reader_preserves_exact_release_replay(single_flow, monkeypatch):
    from millrace.router import control
    from millrace.router.store import new_id

    _, project, home = single_flow
    home.mkdir()
    folder = project / ".millrace-router-owner"
    folder.mkdir()
    instance, run, hold = new_id(), new_id(), new_id()
    owner = folder / "owner.json"
    original = dict(
        instance_id=instance,
        run_id=run,
        hold_id=hold,
        store_path=str(
            control.canonical_path(home) / "instances" / instance / "state.sqlite3"
        ),
    )
    target = project / "regular-owner-target"
    target.write_text(command_graph_json(original))
    owner.symlink_to(target)
    kwargs = dict(
        project_root=project,
        state_home=home,
        hold_id=hold,
        owner_instance_id=instance,
        owner_run_id=run,
        reason="verified stopped",
        stopped=True,
        request_id=new_id(),
    )
    receipt = control.release_project_hold(**kwargs)
    assert receipt["status"] == "applied" and not owner.is_symlink()
    assert json.loads(target.read_bytes()) == original
    replacement = {**original, "hold_id": new_id()}
    owner.write_text(command_graph_json(replacement))
    saved = owner.read_bytes()
    assert control.release_project_hold(**kwargs) == receipt
    assert control.release_project_hold(**kwargs) == receipt
    assert owner.read_bytes() == saved
