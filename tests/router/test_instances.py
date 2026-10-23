"""Instance settings, atomic registration and exact selected-home authority."""

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from tests.router.support import command_graph_json

from millrace.router import control
from millrace.router.records import RouterError
from millrace.router.store import new_id


@pytest.mark.parametrize("timeout", [float("inf"), float("nan")])
def test_nonfinite_settings_python_request_has_typed_framing_refusal(
    flow: tuple[Path, Path, Path], timeout: float
) -> None:
    workflow, project, home = flow
    with pytest.raises(RouterError) as refusal:
        control.create_instance(
            workflow,
            project,
            state_home=home,
            request_id=new_id(),
            expected_state_version=0,
            runtime_defaults={"timeout_seconds": timeout},
        )
    assert refusal.value.code == "nonfinite_json"
    assert not home.exists()


@pytest.mark.parametrize("first,changed", [(False, 0), (1, 1.0)])
def test_creation_settings_request_body_uses_exact_json_identity(flow, first, changed):
    workflow, project, home = flow
    request = new_id()
    receipt = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=request,
        expected_state_version=0,
        runtime_defaults={"timeout_seconds": first},
    )
    with pytest.raises(RouterError) as reused:
        control.create_instance(
            workflow,
            project,
            state_home=home,
            request_id=request,
            expected_state_version=0,
            runtime_defaults={"timeout_seconds": changed},
        )
    assert reused.value.code == "request_id_reused"
    assert (
        control.create_instance(
            workflow,
            project,
            state_home=home,
            request_id=request,
            expected_state_version=0,
            runtime_defaults={"timeout_seconds": first},
        )
        == receipt
    )
    assert len(control.list_instances(state_home=home)) == (first is not False)


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("null", "store_integrity"),
        ("boolean", "store_integrity"),
        ("missing", "store_integrity"),
        ("unknown", "store_integrity"),
        ("wrong_version", "run_integrity"),
        ("wrong_value", "run_integrity"),
    ],
)
def test_frozen_settings_shape_precedes_authority_comparison(
    single_flow, mutation, expected
):
    _, project, home = single_flow
    instance = str(register(single_flow)["instance_id"])
    run = str(start(instance, home)["run_id"])
    before = control.inspect_run(instance, run, state_home=home)
    assert before["status"] == "succeeded"
    assert before["invocations"][0]["writer_state"] == "stopped"
    assert not (project / ".millrace-router-owner/owner.json").exists()
    path = control.load_instance(instance, home).store_path
    with sqlite3.connect(path) as db:
        original = db.execute("SELECT body FROM runs WHERE id=?", (run,)).fetchone()[0]
        body = json.loads(original)
        host = body["host_settings"]
        if mutation == "null":
            host["runtime_defaults"] = None
        elif mutation == "boolean":
            host["operator_policy"] = {"timeout_ceiling_seconds": True}
        elif mutation == "missing":
            del host["operator_policy"]
        elif mutation == "unknown":
            host["runtime_defaults"] = {"unsupported_setting": "unsupported"}
        elif mutation == "wrong_version":
            host["state_version"] = 2
        else:
            host["runtime_defaults"] = {"timeout_seconds": 5}
        db.execute("UPDATE runs SET body=? WHERE id=?", (command_graph_json(body), run))
    try:
        with sqlite3.connect(path) as db:
            corrupt_dump = list(db.iterdump())
        with pytest.raises(RouterError) as refused:
            control.inspect_run(instance, run, state_home=home)
        assert refused.value.code == expected
        with sqlite3.connect(path) as db:
            assert list(db.iterdump()) == corrupt_dump
    finally:
        with sqlite3.connect(path) as db:
            db.execute("UPDATE runs SET body=? WHERE id=?", (original, run))
    assert control.inspect_run(instance, run, state_home=home) == before


def register(
    flow: tuple[Path, Path, Path], request_id: str | None = None
) -> dict[str, object]:
    workflow, project, home = flow
    return control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=request_id or new_id(),
        expected_state_version=0,
    )


def start(instance: str, home: Path, *, mode: str = "locked") -> dict[str, object]:
    version = control.inspect_instance(instance, state_home=home)["state_version"]
    return control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=version,
        edit_mode=mode,
    )


def test_state_home_lookup_config_and_workflow_ownership(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    request = new_id()
    created = register(single_flow, request)
    instance = str(created["instance_id"])
    assert register(single_flow, request) == created
    assert len(control.list_instances(state_home=home)) == 1
    with pytest.raises(RouterError, match="request_id_reused"):
        control.create_instance(
            workflow,
            project,
            state_home=home,
            request_id=request,
            expected_state_version=1,
        )
    duplicate = register(single_flow)
    assert duplicate["code"] == "workflow_registered"
    assert (
        control.create_instance(
            workflow,
            project,
            state_home=home,
            request_id=duplicate["request_id"],
            expected_state_version=0,
        )
        == duplicate
    )
    boot = home / "instances" / instance / "instance.json"
    initial_boot = boot.read_bytes()
    assert set(json.loads(initial_boot)) == {
        "format",
        "instance_id",
        "project_root",
        "workflow_file",
    }
    first_id = new_id()
    first = control.start_run(
        instance, state_home=home, request_id=first_id, expected_state_version=1
    )
    run = str(first["run_id"])
    historical = control.inspect_run(instance, run, state_home=home)
    assert historical["status"] == "succeeded"
    assert (
        control.start_run(
            instance, state_home=home, request_id=first_id, expected_state_version=1
        )
        == first
    )
    assert control.inspect_instance(instance, state_home=home)["state_version"] == 2
    monkeypatch.setenv("ROUTER_CONTROL_TEST", "private-sentinel-value")
    updated = control.update_instance_config(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=2,
        settings={"env_allowlist": ["ROUTER_CONTROL_TEST"]},
    )
    assert updated["status"] == "applied" and updated["state_version"] == 3
    assert boot.read_bytes() == initial_boot
    assert control.inspect_run(instance, run, state_home=home) == historical
    later = start(instance, home)
    later_run = control.inspect_run(instance, str(later["run_id"]), state_home=home)
    assert later_run["host_settings"]["env_allowlist"] == ["ROUTER_CONTROL_TEST"]
    assert later_run["invocations"][0]["env_names"] == ["ROUTER_CONTROL_TEST"]
    assert "private-sentinel-value" not in command_graph_json(later_run)
    with pytest.raises(RouterError) as invalid:
        control.update_instance_config(
            instance,
            state_home=home,
            request_id=new_id(),
            expected_state_version=4,
            settings={"project_root": str(project / "other")},
        )
    assert invalid.value.code == "invalid_fields"
    assert len(control.inspect_instance(instance, state_home=home)["runs"]) == 2
    with pytest.raises(RouterError):
        control.inspect_instance(instance, state_home=home / "other-home")


def test_independent_instances_and_cross_target_refusal(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    a = str(register(single_flow)["instance_id"])
    folder = workflow.parent.parent / "sibling"
    shutil.copytree(workflow.parent, folder)
    other_graph = json.loads((folder / "workflow.json").read_text())
    other_graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "finish": "succeeded"
    }
    (folder / "workflow.json").write_text(command_graph_json(other_graph))
    other_project = project.parent / "other-project"
    other_project.mkdir()
    b = str(register((folder / "workflow.json", other_project, home))["instance_id"])
    a_receipt, b_receipt = start(a, home), start(b, home, mode="live")
    a_run, b_run = str(a_receipt["run_id"]), str(b_receipt["run_id"])
    a_before = control.inspect_run(a, a_run, state_home=home)
    b_before = control.inspect_run(b, b_run, state_home=home)
    assert a_before["status"] == "paused" and b_before["status"] == "succeeded"
    bad = control.retry_invocation(
        a,
        a_run,
        invocation_id=b_before["invocations"][0]["id"],
        reason="wrong-owner",
        state_home=home,
        request_id=new_id(),
        expected_state_version=a_before["state_version"],
        plane_id="main",
    )
    assert bad["code"] == "invalid_target"
    assert (
        control.inspect_run(a, a_run, state_home=home)["state_version"]
        == a_before["state_version"]
    )
    assert control.inspect_run(b, b_run, state_home=home) == b_before
    refused = control.update_instance_config(
        a,
        state_home=home,
        request_id=new_id(),
        expected_state_version=2,
        settings={"env_allowlist": ["NEW_NAME"]},
    )
    assert refused["code"] == "run_nonterminal"
    with pytest.raises(RouterError, match="unknown_run"):
        control.inspect_run(b, a_run, state_home=home)
    control.cancel_run(a, a_run, state_home=home, request_id=new_id())
    assert control.inspect_run(a, a_run, state_home=home)["status"] == "cancelled"
    assert control.inspect_run(b, b_run, state_home=home) == b_before
    assert not (project / ".millrace-router-owner/owner.json").exists()


@pytest.mark.parametrize("field,value", [("mode", "live"), ("state_version", 999)])
def test_mutated_start_authority_and_version_refuse(
    single_flow: tuple[Path, Path, Path], field: str, value: object
) -> None:
    instance = str(register(single_flow)["instance_id"])
    home = single_flow[2]
    receipt = start(instance, home)
    run = str(receipt["run_id"])
    config = control.load_instance(instance, home)
    connection = sqlite3.connect(config.store_path)
    body = json.loads(connection.execute("SELECT body FROM runs").fetchone()[0])
    body[field] = value
    connection.execute("UPDATE runs SET body=?", (command_graph_json(body),))
    connection.commit()
    before = list(connection.iterdump())
    connection.close()
    with pytest.raises(RouterError):
        control.inspect_run(instance, run, state_home=home)
    connection = sqlite3.connect(config.store_path)
    assert list(connection.iterdump()) == before
    connection.close()


def test_symlinked_state_root_cannot_alias_instance(
    single_flow: tuple[Path, Path, Path],
) -> None:
    instance = str(register(single_flow)["instance_id"])
    home = single_flow[2]
    other = home.parent / "alias-home"
    (other / "instances").mkdir(parents=True)
    (other / "instances" / instance).symlink_to(
        home / "instances" / instance, target_is_directory=True
    )
    with pytest.raises(RouterError):
        control.inspect_instance(instance, state_home=other)


def test_creation_publication_crash_has_no_registered_half_instance(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    request = new_id()

    class Crash(BaseException):
        pass

    def fail(*args: object) -> None:
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr("millrace.router.control.os.rename", fail)
        with pytest.raises(Crash):
            register(single_flow, request)
    assert control.list_instances(state_home=home) == []
    assert list(home.glob(".creating-*")) == []
    created = register(single_flow, request)
    assert (
        register(single_flow, request) == created
        and len(control.list_instances(state_home=home)) == 1
    )
    workflow.write_text("invalid edited draft")
    assert register(single_flow, request) == created  # dedup before current preflight


def test_creation_semantic_refusal_is_final_before_ambient_repair(
    single_flow: tuple[Path, Path, Path],
) -> None:
    import sys

    workflow, project, home = single_flow
    alias = project.parent / "project-alias"
    alias.symlink_to(project, target_is_directory=True)
    request: dict[str, object] = {
        "format": "millrace-router-request/v2",
        "operation": "create_instance",
        "instance_id": None,
        "run_id": None,
        "plane_id": None,
        "request_id": new_id(),
        "expected_state_version": 0,
        "payload": {
            "workflow_file": str(workflow),
            "project_root": str(alias),
            "path": [str(Path(sys.executable).parent)],
            "env_allowlist": [],
            "host_paths": {},
        },
    }
    refused = control.execute_request(request, state_home=home)
    assert refused["status"] == "refused" and refused["code"] == "invalid_roots"
    alias.unlink()
    alias.mkdir()
    assert control.execute_request(request, state_home=home) == refused
    assert control.list_instances(state_home=home) == []
    fresh = {**request, "request_id": new_id()}
    assert control.execute_request(fresh, state_home=home)["status"] == "applied"


def test_creation_id_dedup_precedes_changed_body_settings(
    single_flow: tuple[Path, Path, Path],
) -> None:
    instance = register(single_flow)
    with pytest.raises(RouterError) as refusal:
        control.create_instance(
            single_flow[0],
            single_flow[1],
            state_home=single_flow[2],
            request_id=instance["request_id"],
            expected_state_version=0,
            path=(".",),
        )
    assert refusal.value.code == "request_id_reused"


def test_creation_response_gap_replays_published_receipt(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    workflow, project, home = single_flow
    request = new_id()
    rename = os.rename

    class Crash(BaseException):
        pass

    def published(*args: object) -> None:
        rename(*args)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr("millrace.router.control.os.rename", published)
        with pytest.raises(Crash):
            register(single_flow, request)
    listed = control.list_instances(state_home=home)
    assert len(listed) == 1 and listed[0]["state_version"] == 1
    # Even a new semantic preflight failure cannot reactivate this final request.
    workflow.write_text("invalid draft after publication")
    receipt = register(single_flow, request)
    assert (
        receipt["status"] == "applied"
        and receipt["instance_id"] == listed[0]["instance_id"]
    )
    assert register(single_flow, request) == receipt
    assert control.list_instances(state_home=home) == listed


@pytest.mark.parametrize("alias_kind", ["case", "firmlink"])
def test_filesystem_aliases_share_home_and_workflow_identity(single_flow, alias_kind):
    import sys

    from millrace.router import control
    from millrace.router.store import new_id

    workflow, project, home = single_flow
    home.mkdir()
    if alias_kind == "case":
        alias = home.with_name(home.name.swapcase())
        other_workflow = (
            workflow.parent.with_name(workflow.parent.name.swapcase()) / workflow.name
        )
    else:
        if sys.platform != "darwin":
            pytest.skip("Darwin firmlink proof")
        alias = Path("/System/Volumes/Data") / str(home.resolve()).lstrip("/")
        other_workflow = Path("/System/Volumes/Data") / str(workflow.resolve()).lstrip(
            "/"
        )
    if not alias.exists() or not alias.samefile(home):
        pytest.skip("filesystem does not provide this alias")
    request = new_id()
    first = control.create_instance(
        workflow,
        project,
        state_home=alias,
        request_id=request,
        expected_state_version=0,
    )
    instance = first["instance_id"]
    assert (
        control.create_instance(
            other_workflow,
            project,
            state_home=home,
            request_id=request,
            expected_state_version=0,
        )
        == first
    )
    assert control.inspect_instance(
        instance, state_home=home
    ) == control.inspect_instance(instance, state_home=alias)
    duplicate = control.create_instance(
        other_workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    assert duplicate["code"] == "workflow_registered"
    assert len(control.list_instances(state_home=alias)) == 1


def test_deleted_registered_workflow_does_not_block_independent_create(single_flow):
    workflow, project, home = single_flow
    first = register(single_flow)
    other = workflow.parent.with_name("independent-workflow")
    shutil.copytree(workflow.parent, other)
    shutil.rmtree(workflow.parent)
    request = new_id()
    created = register((other / workflow.name, project, home), request)
    assert created["status"] == "applied"
    assert created["instance_id"] != first["instance_id"]
    assert len(control.list_instances(state_home=home)) == 2
    # Later folder repair or draft changes cannot change the published receipt.
    shutil.copytree(other, workflow.parent)
    (other / workflow.name).write_text("invalid current draft")
    assert register((other / workflow.name, project, home), request) == created
    assert register((other / workflow.name, project, home), request) == created
    assert (
        control.inspect_instance(str(first["instance_id"]), state_home=home)[
            "state_version"
        ]
        == 1
    )
    assert (
        control.inspect_instance(str(created["instance_id"]), state_home=home)[
            "state_version"
        ]
        == 1
    )


def test_registered_workflow_identity_error_is_a_final_refusal(
    single_flow, monkeypatch
):
    workflow, project, home = single_flow
    register(single_flow)
    other = workflow.parent.with_name("independent-workflow")
    shutil.copytree(workflow.parent, other)
    request = new_id()
    original = Path.samefile

    def denied(path, other_path):
        if path == workflow.parent:
            raise PermissionError("registered folder identity unavailable")
        return original(path, other_path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "samefile", denied)
        refused = register((other / workflow.name, project, home), request)
    assert refused["status"] == "refused" and refused["code"] == "instance_integrity"
    assert register((other / workflow.name, project, home), request) == refused
    assert register((other / workflow.name, project, home), request) == refused
    assert len(control.list_instances(state_home=home)) == 1
    assert register((other / workflow.name, project, home))["status"] == "applied"
    assert len(control.list_instances(state_home=home)) == 2


@pytest.mark.parametrize("already_registered", [False, True])
def test_regular_finder_metadata_does_not_block_creation(
    single_flow, already_registered
):
    workflow, project, home = single_flow
    known = []
    if already_registered:
        known.append(register(single_flow)["instance_id"])
        other = workflow.parent.with_name("independent-workflow")
        shutil.copytree(workflow.parent, other)
        workflow = other / workflow.name
    folder = home / "instances"
    folder.mkdir(parents=True, exist_ok=True)
    metadata = folder / ".DS_Store"
    raw = b"owned Finder metadata\x00\xff"
    metadata.write_bytes(raw)
    created = register((workflow, project, home))
    assert created["status"] == "applied"
    known.append(created["instance_id"])
    assert sorted(
        row["instance_id"] for row in control.list_instances(state_home=home)
    ) == sorted(known)
    duplicate = register((workflow, project, home))
    assert (
        duplicate["status"] == "refused" and duplicate["code"] == "workflow_registered"
    )
    assert register((workflow, project, home), duplicate["request_id"]) == duplicate
    assert metadata.read_bytes() == raw and not metadata.is_symlink()
    assert len(control.list_instances(state_home=home)) == len(known)


def test_regular_finder_metadata_does_not_change_listing(single_flow):
    register(single_flow)
    home = single_flow[2]
    before = control.list_instances(state_home=home)
    metadata = home / "instances/.DS_Store"
    raw = b"owned Finder metadata after registration\n"
    metadata.write_bytes(raw)
    assert control.list_instances(state_home=home) == before
    assert metadata.read_bytes() == raw and not metadata.is_symlink()


@pytest.mark.parametrize("operation", ["create", "list"])
@pytest.mark.parametrize("error_kind", ["missing", "denied"])
def test_finder_metadata_lstat_race_has_structured_aftermath(
    single_flow, monkeypatch, operation, error_kind
):
    home = single_flow[2]
    before = []
    if operation == "list":
        register(single_flow)
        before = control.list_instances(state_home=home)
    folder = home / "instances"
    folder.mkdir(parents=True, exist_ok=True)
    metadata = folder / ".DS_Store"
    raw = b"Finder metadata racing with directory enumeration\n"
    metadata.write_bytes(raw)
    original_lstat = Path.lstat
    attempts = []

    def raced_lstat(path, *args, **kwargs):
        if path == metadata:
            attempts.append(path)
            if error_kind == "missing":
                metadata.unlink()
                raise FileNotFoundError("owned metadata disappeared")
            raise PermissionError("owned metadata is unreadable")
        return original_lstat(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", raced_lstat)
        if operation == "create":
            result = register(single_flow)
            if error_kind == "missing":
                assert result["status"] == "applied"
            else:
                assert result["status"] == "refused"
                assert result["code"] == "instance_integrity"
        elif error_kind == "missing":
            assert control.list_instances(state_home=home) == before
        else:
            with pytest.raises(RouterError) as caught:
                control.list_instances(state_home=home)
            assert caught.value.code == "instance_integrity"
    assert attempts == [metadata]
    if error_kind == "missing":
        assert not metadata.exists()
    else:
        assert metadata.read_bytes() == raw


@pytest.mark.parametrize(
    ("name", "kind", "create_code", "list_code"),
    [
        ("unexpected", "file", "instance_integrity", "invalid_id"),
        (".metadata", "file", "instance_integrity", "invalid_id"),
        (".DS_Store.tmp", "file", "instance_integrity", "invalid_id"),
        (".ds_store", "file", "instance_integrity", "invalid_id"),
        ("unexpected", "directory", "invalid_id", "invalid_id"),
        (".DS_Store", "directory", "invalid_id", "invalid_id"),
        (".DS_Store", "symlink_file", "instance_integrity", "invalid_id"),
        (".DS_Store", "symlink_directory", "invalid_id", "invalid_id"),
        (".DS_Store", "dangling_symlink", "instance_integrity", "invalid_id"),
        (".DS_Store", "fifo", "instance_integrity", "invalid_id"),
        ("a" * 32, "corrupt_directory", "instance_integrity", "instance_integrity"),
        ("a" * 32, "file", "instance_integrity", "instance_integrity"),
        ("a" * 32, "symlink_directory", "instance_integrity", "instance_integrity"),
    ],
)
def test_finder_metadata_exception_preserves_instance_guards(
    single_flow, name, kind, create_code, list_code
):
    workflow, project, home = single_flow
    first = register(single_flow)
    before = control.inspect_instance(first["instance_id"], state_home=home)
    folder = home / "instances"
    entry = folder / name
    if kind == "file":
        entry.write_bytes(b"unexpected registry entry")
    elif kind in {"directory", "corrupt_directory"}:
        entry.mkdir()
        if kind == "corrupt_directory":
            (entry / "instance.json").write_text("not valid JSON")
    elif kind == "fifo":
        os.mkfifo(entry)
    elif kind == "symlink_directory":
        entry.symlink_to(folder / first["instance_id"], target_is_directory=True)
    else:
        target = project / "metadata-target"
        if kind == "symlink_file":
            target.write_bytes(b"regular target does not make a symlink regular")
        entry.symlink_to(target)
    entries = sorted(path.name for path in folder.iterdir())
    other = workflow.parent.with_name("independent-workflow")
    shutil.copytree(workflow.parent, other)
    refused = register((other / workflow.name, project, home))
    assert refused["status"] == "refused" and refused["code"] == create_code
    assert (
        register((other / workflow.name, project, home), refused["request_id"])
        == refused
    )
    with pytest.raises(RouterError) as invalid:
        control.list_instances(state_home=home)
    assert invalid.value.code == list_code
    assert sorted(path.name for path in folder.iterdir()) == entries
    assert control.inspect_instance(first["instance_id"], state_home=home) == before


def test_finder_metadata_repair_preserves_final_refusal(single_flow):
    workflow, project, home = single_flow
    metadata = home / "instances/.DS_Store"
    metadata.mkdir(parents=True)
    request = new_id()
    refused = register(single_flow, request)
    assert refused["status"] == "refused" and refused["code"] == "invalid_id"
    receipt_path = home / "creation-receipts" / (request + ".json")
    raw_receipt = receipt_path.read_bytes()
    metadata.rmdir()
    raw_metadata = b"corrected to a regular Finder metadata entry\n"
    metadata.write_bytes(raw_metadata)
    assert register(single_flow, request) == refused
    assert receipt_path.read_bytes() == raw_receipt
    with pytest.raises(RouterError) as reused:
        control.create_instance(
            workflow,
            project,
            state_home=home,
            request_id=request,
            expected_state_version=1,
        )
    assert reused.value.code == "request_id_reused"
    assert receipt_path.read_bytes() == raw_receipt
    created = register(single_flow)
    assert created["status"] == "applied"
    assert len(control.list_instances(state_home=home)) == 1
    assert metadata.read_bytes() == raw_metadata
    assert register(single_flow, request) == refused
    assert receipt_path.read_bytes() == raw_receipt
