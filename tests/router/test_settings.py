"""P3a settings authority, selected context and durable launch acceptance."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from tests.router.support import command_graph_json

from millrace.router import control
from millrace.router.graph import capture, validate_workflow
from millrace.router.records import CommandResult, RouterError
from millrace.router.store import Store, new_id


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _graph(workflow: Path) -> dict[str, object]:
    return _object(json.loads(workflow.read_text()))


def _invoke(graph: dict[str, object]) -> dict[str, object]:
    return _object(
        _object(_object(graph["planes"]["main"]["nodes"])["draft"])["invoke"]
    )


def _register(
    flow: tuple[Path, Path, Path],
    *,
    runtime: dict[str, object] | None = None,
    policy: dict[str, object] | None = None,
) -> str:
    workflow, project, home = flow
    receipt = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        runtime_defaults=runtime or {},
        operator_policy=policy or {},
    )
    assert receipt["status"] == "applied", receipt
    return str(receipt["instance_id"])


def _items(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [_object(item) for item in value]


def _version(value: dict[str, object]) -> int:
    version = value["state_version"]
    assert type(version) is int
    return version


def _start(instance: str, home: Path, *, mode: str = "locked") -> dict[str, object]:
    receipt = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=_version(
            control.inspect_instance(instance, state_home=home)
        ),
        edit_mode=mode,
    )
    assert receipt["status"] == "applied", receipt
    return control.inspect_run(instance, str(receipt["run_id"]), state_home=home)


def _resolved(validation: dict[str, object]) -> dict[str, object]:
    assert validation["valid"] is True, validation
    return _object(
        _object(_object(_object(validation["resolved_nodes"])["main"])["draft"])[
            "common"
        ]
    )


def _database(home: Path, instance: str) -> Path:
    return home / "instances" / instance / "state.sqlite3"


def _dump(path: Path) -> str:
    with sqlite3.connect(path) as db:
        return "\n".join(db.iterdump())


@pytest.mark.parametrize(
    "defaults,override,effective,source,warned",
    [
        ({}, None, 1800, "fallback", True),
        ({"timeout_seconds": 600}, None, 600, "workflow", False),
        ({"timeout_seconds": 600}, 120, 120, "node", False),
        ({}, 1800, 1800, "node", False),
    ],
)
def test_layer_precedence_fallback_and_policy(
    single_flow: tuple[Path, Path, Path],
    defaults: dict[str, object],
    override: int | None,
    effective: int,
    source: str,
    warned: bool,
) -> None:
    workflow, _, home = single_flow
    graph = _graph(workflow)
    if defaults:
        graph["defaults"] = defaults
    invoke = _invoke(graph)
    invoke.pop("timeout_seconds")
    if override is not None:
        invoke["timeout_seconds"] = override
    workflow.write_text(command_graph_json(graph))
    instance = _register(single_flow)
    validation = control.validate_revision(instance, state_home=home)
    assert validation["valid"] is True, validation
    settings = _resolved(validation)
    assert settings == {
        "plane_id": "main",
        "producing_revision": validation["revision_id"],
        "runtime_settings_version": 1,
        "requested": {"binding": "python", "timeout_seconds": effective},
        "effective": {"binding": "python", "timeout_seconds": effective},
        "provenance": {
            "binding": {"source": "node", "policy": None},
            "timeout_seconds": {"source": source, "policy": None},
        },
        "warnings": (
            [
                {
                    "code": "timeout_defaulted",
                    "plane_id": "main",
                    "node_id": "draft",
                    "field": "timeout_seconds",
                }
            ]
            if warned
            else []
        ),
    }
    assert validation["warnings"] == settings["warnings"]
    current = control.inspect_instance(instance, state_home=home)
    assert current["runtime_defaults"] == {}
    assert current["operator_policy"] == {}


@pytest.mark.parametrize(
    "runtime,defaults,overrides,binding_source,timeout_source,requested",
    [
        (
            {"binding": "python", "timeout_seconds": 900},
            {},
            {},
            "runtime",
            "runtime",
            900,
        ),
        (
            {"binding": "python", "timeout_seconds": 900},
            {"binding": "alternate", "timeout_seconds": 600},
            {"binding": "python", "timeout_seconds": 120},
            "node",
            "node",
            120,
        ),
        (
            {"binding": "alternate", "timeout_seconds": 900},
            {"binding": "python", "timeout_seconds": 600},
            {},
            "workflow",
            "workflow",
            600,
        ),
        ({"binding": "python"}, {}, {}, "runtime", "fallback", 1800),
    ],
)
def test_runtime_selection_and_policy_constraints(
    single_flow: tuple[Path, Path, Path],
    runtime: dict[str, object],
    defaults: dict[str, object],
    overrides: dict[str, object],
    binding_source: str,
    timeout_source: str,
    requested: int,
) -> None:
    workflow, _, home = single_flow
    graph = _graph(workflow)
    _object(graph["bindings"])["alternate"] = _object(graph["bindings"])["python"]
    graph["defaults"] = defaults
    invoke = _invoke(graph)
    invoke.pop("binding")
    invoke.pop("timeout_seconds")
    invoke.update(overrides)
    workflow.write_text(command_graph_json(graph))
    instance = _register(
        single_flow,
        runtime=runtime,
        policy={"binding_pin": "python", "timeout_ceiling_seconds": 300},
    )
    settings = _resolved(control.validate_revision(instance, state_home=home))
    assert settings["requested"] == {"binding": "python", "timeout_seconds": requested}
    assert settings["effective"] == {
        "binding": "python",
        "timeout_seconds": min(requested, 300),
    }
    assert settings["provenance"] == {
        "binding": {
            "source": binding_source,
            "policy": {"kind": "pin", "value": "python"},
        },
        "timeout_seconds": {
            "source": timeout_source,
            "policy": {"kind": "ceiling", "value": 300},
        },
    }
    assert bool(settings["warnings"]) is (timeout_source == "fallback")
    result = _start(instance, home)
    launch = _object(_items(result["invocations"])[0]["launch_settings"]["common"])
    assert launch == {
        **settings,
        "producing_revision": _items(result["invocations"])[0]["revision_id"],
    }
    assert result["status"] == "succeeded"
    assert result == control.inspect_run(instance, str(result["id"]), state_home=home)


@pytest.mark.parametrize(
    "location,field,value,code",
    [
        ("runtime", "timeout_seconds", None, "invalid_settings"),
        ("runtime", "timeout_seconds", False, "invalid_settings"),
        ("runtime", "timeout_seconds", 0, "invalid_settings"),
        ("runtime", "binding", "bad name", "invalid_settings"),
        ("runtime", "extra", 1, "invalid_settings"),
        ("policy", "binding_pin", None, "invalid_settings"),
        ("policy", "timeout_ceiling_seconds", -1, "invalid_settings"),
        ("policy", "extra", 1, "invalid_settings"),
        ("defaults", "timeout_seconds", "600", "invalid_settings"),
        ("defaults", "binding", "", "invalid_settings"),
        ("defaults", "extra", 1, "invalid_settings"),
        ("invoke", "timeout_seconds", True, "invalid_settings"),
        ("invoke", "binding", None, "invalid_settings"),
        ("invoke", "extra", 1, "invalid_fields"),
    ],
)
def test_invalid_settings_refusal_is_final_and_shadowed_values_still_fail(
    single_flow: tuple[Path, Path, Path],
    location: str,
    field: str,
    value: object,
    code: str,
) -> None:
    workflow, project, home = single_flow
    original = workflow.read_bytes()
    graph = _graph(workflow)
    runtime: dict[str, object] = {}
    policy: dict[str, object] = {}
    if location == "runtime":
        runtime[field] = value
    elif location == "policy":
        policy[field] = value
    elif location == "defaults":
        graph["defaults"] = {field: value}
    else:
        _invoke(graph)[field] = value
    workflow.write_text(command_graph_json(graph))
    request = new_id()

    def create() -> dict[str, object]:
        return control.create_instance(
            workflow,
            project,
            state_home=home,
            request_id=request,
            expected_state_version=0,
            runtime_defaults=runtime,
            operator_policy=policy,
        )

    receipt = create()
    assert receipt["status"] == "refused" and receipt["code"] == code, receipt
    assert receipt["state_version"] == 0
    workflow.write_bytes(original)
    assert create() == receipt
    assert not list((home / "instances").glob("*/instance.json"))


@pytest.mark.parametrize("missing", [True, False])
def test_binding_pin_does_not_supply_or_rewrite_a_binding(
    single_flow: tuple[Path, Path, Path], missing: bool
) -> None:
    workflow, project, home = single_flow
    graph = _graph(workflow)
    if missing:
        _invoke(graph).pop("binding")
    workflow.write_text(command_graph_json(graph))
    receipt = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        operator_policy={"binding_pin": "other"},
    )
    assert receipt["status"] == "refused"
    assert receipt["code"] == (
        "missing_effective_binding" if missing else "binding_pin_conflict"
    )


@pytest.mark.parametrize("layer", ["runtime_defaults", "operator_policy"])
def test_explicit_null_configuration_object_is_not_omission(
    single_flow: tuple[Path, Path, Path], layer: str
) -> None:
    workflow, project, home = single_flow
    request = new_id()

    def create() -> dict[str, object]:
        if layer == "runtime_defaults":
            return control.create_instance(
                workflow,
                project,
                state_home=home,
                request_id=request,
                expected_state_version=0,
                runtime_defaults=None,
            )
        return control.create_instance(
            workflow,
            project,
            state_home=home,
            request_id=request,
            expected_state_version=0,
            operator_policy=None,
        )

    refused = create()
    assert refused["status"] == "refused" and refused["code"] == "invalid_settings"
    assert create() == refused
    assert not list((home / "instances").glob("*/instance.json"))


def test_invalid_validation_never_exposes_partial_resolution(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, _, home = single_flow
    graph = _graph(workflow)
    first = _object(_object(graph["planes"]["main"]["nodes"])["draft"])
    _object(first["invoke"]).pop("timeout_seconds")
    second = _object(json.loads(command_graph_json(first)))
    first["on"] = {"READY": {"goto": "a"}}
    for node in [first, second]:
        node["on_error"] = {"finish": "failed"}
        node["on_unknown"] = {"finish": "failed"}
    graph["planes"]["main"]["start"] = "z"
    graph["planes"]["main"]["nodes"] = {"z": first, "a": second}
    workflow.write_text(command_graph_json(graph))
    instance = _register(single_flow)
    valid = control.validate_revision(instance, state_home=home)
    assert valid["valid"] is True
    assert valid["warnings"] == [
        {
            "code": "timeout_defaulted",
            "plane_id": "main",
            "node_id": node,
            "field": "timeout_seconds",
        }
        for node in ["a", "z"]
    ]
    _object(second["invoke"]).pop("binding")
    workflow.write_text(command_graph_json(graph))
    invalid = control.validate_revision(instance, state_home=home)
    assert invalid["valid"] is False
    assert invalid["resolved_nodes"] == {} and invalid["warnings"] == []
    assert invalid["validation_context"] == {
        "kind": "instance",
        "instance_id": instance,
        "run_id": None,
        "settings_version": 1,
    }


def test_selected_validation_context_and_isolation(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    graph = _graph(workflow)
    _invoke(graph).pop("timeout_seconds")
    workflow.write_text(command_graph_json(graph))
    raw = capture(workflow, project)
    instance = _register(single_flow, runtime={"timeout_seconds": 6})
    first = _start(instance, home)
    run = str(first["id"])
    original = control.validate_revision(instance, run_id=run, state_home=home)
    assert original["validation_context"] == {
        "kind": "run",
        "instance_id": instance,
        "run_id": run,
        "settings_version": 1,
        "run_state_version": first["state_version"],
    }
    assert _object(_resolved(original)["effective"])["timeout_seconds"] == 6
    changed = control.update_instance_config(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=_version(
            control.inspect_instance(instance, state_home=home)
        ),
        settings={"runtime_defaults": {"timeout_seconds": 9}},
    )
    assert changed["status"] == "applied"
    current = control.validate_revision(instance, state_home=home)
    assert (
        _object(current["validation_context"])["settings_version"]
        == changed["state_version"]
    )
    assert _object(_resolved(current)["effective"])["timeout_seconds"] == 9
    assert control.validate_revision(instance, run_id=run, state_home=home) == original
    assert capture(workflow, project).revision_id == raw.revision_id
    assert control.inspect_run(instance, run, state_home=home) == first

    other_folder = workflow.parent.parent / "other-workflow"
    shutil.copytree(workflow.parent, other_folder)
    other_project = project.parent / "other-project"
    other_project.mkdir()
    other = _register(
        (other_folder / "workflow.json", other_project, home),
        runtime={"timeout_seconds": 3},
    )
    assert (
        _object(
            _resolved(control.validate_revision(other, state_home=home))["effective"]
        )["timeout_seconds"]
        == 3
    )
    other_run = _start(other, home)
    with pytest.raises(RouterError):
        control.validate_revision(
            instance, run_id=str(other_run["id"]), state_home=home
        )

    # Missing effective binding is runnable-invalid, but raw revision capture succeeds.
    _invoke(graph).pop("binding")
    workflow.write_text(command_graph_json(graph))
    draft = capture(workflow, project)
    invalid = control.validate_revision(instance, run_id=run, state_home=home)
    assert invalid["valid"] is False
    assert invalid["revision_id"] == draft.revision_id
    assert invalid["resolved_nodes"] == {} and invalid["warnings"] == []
    assert "missing_effective_binding" in cast(list[str], invalid["diagnostics"])
    assert _object(invalid["validation_context"])["settings_version"] == 1
    inspection = control.inspect_run(instance, run, state_home=home)
    assert inspection["state_version"] == first["state_version"]
    assert _object(inspection["draft_revision"])["revision_id"] is None
    assert (
        _object(inspection["draft_revision"])["diagnostic"]
        == "missing_effective_binding"
    )
    with pytest.raises(RouterError, match="context_required"):
        validate_workflow(workflow, project)
    assert (
        validate_workflow(
            workflow,
            project,
            settings_context={
                "state_version": 1,
                "runtime_defaults": {"binding": "python"},
                "operator_policy": {},
            },
        )
        is True
    )


def test_runtime_configuration_receipts_and_idle_boundary(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, _, home = single_flow
    instance = _register(single_flow)
    boot = home / "instances" / instance / "instance.json"
    original_boot = boot.read_bytes()
    request = new_id()
    settings: dict[str, object] = {
        "runtime_defaults": {"timeout_seconds": 7},
        "operator_policy": {"timeout_ceiling_seconds": 5},
    }
    applied = control.update_instance_config(
        instance,
        state_home=home,
        request_id=request,
        expected_state_version=1,
        settings=settings,
    )
    assert applied["status"] == "applied" and applied["state_version"] == 2
    assert (
        control.update_instance_config(
            instance,
            state_home=home,
            request_id=request,
            expected_state_version=1,
            settings=settings,
        )
        == applied
    )
    with pytest.raises(RouterError, match="request_id_reused"):
        control.update_instance_config(
            instance,
            state_home=home,
            request_id=request,
            expected_state_version=1,
            settings={"runtime_defaults": {}},
        )
    cleared = control.update_instance_config(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=2,
        settings={"runtime_defaults": {}},
    )
    assert cleared["status"] == "applied"
    current = control.inspect_instance(instance, state_home=home)
    assert current["runtime_defaults"] == {}
    assert current["operator_policy"] == {"timeout_ceiling_seconds": 5}
    assert boot.read_bytes() == original_boot
    stale = control.update_instance_config(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=2,
        settings={"operator_policy": {}},
    )
    assert stale["status"] == "refused" and stale["code"] == "stale_state_version"
    graph = _graph(workflow)
    _object(_object(graph["planes"]["main"]["nodes"])["draft"])["on"] = {
        "READY": {"pause": {"reason": "review", "resume": "draft"}}
    }
    workflow.write_text(command_graph_json(graph))
    paused = _start(instance, home)
    run = str(paused["id"])
    assert paused["status"] == "paused"
    before = control.inspect_instance(instance, state_home=home)
    try:
        refused = control.update_instance_config(
            instance,
            state_home=home,
            request_id=new_id(),
            expected_state_version=_version(before),
            settings={"operator_policy": {}},
        )
        assert refused["status"] == "refused" and refused["code"] == "run_nonterminal"
        assert _version(
            control.inspect_instance(instance, state_home=home)
        ) == _version(before)
        assert (
            control.inspect_run(instance, run, state_home=home)["state_version"]
            == paused["state_version"]
        )
    finally:
        control.cancel_run(instance, run, state_home=home, request_id=new_id())
    idle = control.update_instance_config(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=_version(
            control.inspect_instance(instance, state_home=home)
        ),
        settings={"operator_policy": {}},
    )
    assert idle["status"] == "applied"
    assert control.inspect_instance(instance, state_home=home)["operator_policy"] == {}
    assert boot.read_bytes() == original_boot


@pytest.mark.parametrize("mode", ["locked", "live"])
def test_effective_launch_settings_and_edit_aftermath(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    workflow, project, home = single_flow
    graph = _graph(workflow)
    _invoke(graph).pop("timeout_seconds")
    graph["defaults"] = {"timeout_seconds": 3}
    workflow.write_text(command_graph_json(graph))
    instance = _register(single_flow, policy={"timeout_ceiling_seconds": 5})
    calls = 0

    def invoke(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        deadline: float,
        cancelled: Callable[[], bool],
        on_started: Callable[[int], None],
    ) -> CommandResult:
        nonlocal calls
        calls += 1
        on_started(45678)
        assert cwd == project and argv and env.get("PATH") and deadline > 0
        if calls == 1:
            graph["defaults"] = {"timeout_seconds": 9}
            workflow.write_text(command_graph_json(graph))
            return CommandResult(
                b"",
                b"failed first attempt",
                "execution_failure",
                "stopped",
                exit_code=7,
            )
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    paused = _start(instance, home, mode=mode)
    run = str(paused["id"])
    assert paused["status"] == "paused" and paused["count"] == 1
    first = _items(paused["invocations"])[0]
    first_launch = _object(first["launch_settings"]["common"])
    assert _object(first_launch["effective"])["timeout_seconds"] == 3
    assert first["deadline"] == min(
        cast(float, paused["deadline"]), cast(float, first["created_at"]) + 3
    )
    if mode == "locked":
        pending = _object(paused["pending_revision"])
        receipt = control.accept_revision(
            instance,
            run,
            state_home=home,
            request_id=new_id(),
            expected_state_version=_version(paused),
            candidate_id=str(pending["candidate_id"]),
        )
        assert receipt["status"] == "applied"
        paused = control.inspect_run(instance, run, state_home=home)
    retry = control.retry_invocation(
        instance,
        run,
        invocation_id=str(first["id"]),
        reason="checked_failure",
        state_home=home,
        request_id=new_id(),
        expected_state_version=_version(paused),
        plane_id="main",
    )
    assert retry["status"] == "applied", retry
    finished = control.inspect_run(instance, run, state_home=home)
    assert calls == 2 and finished["status"] == "succeeded" and finished["count"] == 2
    assert finished["started_at"] == paused["started_at"]
    assert (
        finished["deadline"] == paused["deadline"] and finished["cap"] == paused["cap"]
    )
    launches = _items(finished["invocations"])
    assert launches[0]["launch_settings"]["common"] == first_launch
    assert (
        _object(_object(launches[1]["launch_settings"]["common"])["effective"])[
            "timeout_seconds"
        ]
        == 5
    )
    assert launches[0]["revision_id"] != launches[1]["revision_id"]
    assert finished == control.inspect_run(instance, run, state_home=home)
    assert not (project / ".millrace-router-owner/owner.json").exists()


@pytest.mark.parametrize(
    "corruption,expected",
    [
        ("missing", "invocation_integrity"),
        ("format", "invocation_integrity"),
        ("requested", "invocation_integrity"),
        ("effective_and_deadline", "invocation_integrity"),
        ("source", "invocation_integrity"),
        ("policy", "invocation_integrity"),
        ("warnings", "invocation_integrity"),
        ("version", "invocation_integrity"),
        ("argv", "invocation_integrity"),
        ("deadline", "counter_integrity"),
    ],
)
def test_settings_format_boundary_and_provenance_corruption(
    single_flow: tuple[Path, Path, Path], corruption: str, expected: str
) -> None:
    _, _, home = single_flow
    instance = _register(single_flow)
    finished = _start(instance, home)
    run = str(finished["id"])
    db_path = _database(home, instance)
    with sqlite3.connect(db_path) as db:
        assert (
            db.execute("SELECT value FROM meta WHERE key='format'").fetchone()[0]
            == "millrace-router-store/v7"
        )
        body = _object(
            json.loads(db.execute("SELECT body FROM invocations").fetchone()[0])
        )
        launch = _object(body["launch_settings"]["common"])
        if corruption == "missing":
            body.pop("launch_settings")
        elif corruption == "format":
            _object(body["launch_settings"])["format"] = (
                "millrace-router-launch-settings/v0"
            )
        elif corruption == "requested":
            _object(launch["requested"])["binding"] = "other"
        elif corruption == "effective_and_deadline":
            _object(launch["effective"])["timeout_seconds"] = 999
            body["deadline"] = 1
        elif corruption == "source":
            _object(_object(launch["provenance"])["timeout_seconds"])["source"] = (
                "runtime"
            )
        elif corruption == "policy":
            _object(_object(launch["provenance"])["binding"])["policy"] = {
                "kind": "pin",
                "value": "python",
            }
        elif corruption == "warnings":
            launch["warnings"] = [
                {
                    "code": "timeout_defaulted",
                    "plane_id": "main",
                    "node_id": "draft",
                    "field": "timeout_seconds",
                }
            ]
        elif corruption == "version":
            launch["runtime_settings_version"] = 2
        elif corruption == "argv":
            body["argv"] = ["/invalid/executable"]
        else:
            body["deadline"] = 1
        db.execute("UPDATE invocations SET body=?", (command_graph_json(body),))
    before = _dump(db_path)
    with pytest.raises(RouterError) as refused:
        control.inspect_run(instance, run, state_home=home)
    assert refused.value.code == expected
    assert _dump(db_path) == before


def test_fresh_home_refuses_v3_without_migration(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    instance = _register(single_flow)
    _start(instance, home)
    db_path = _database(home, instance)
    boot = _object(
        json.loads((home / "instances" / instance / "instance.json").read_text())
    )
    assert boot["format"] == "millrace-router-instance/v2"
    assert set(boot) == {"format", "instance_id", "project_root", "workflow_file"}
    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE meta SET value='millrace-router-store/v3' WHERE key='format'"
        )
    before = _dump(db_path)
    for inspect in (
        lambda: control.inspect_instance(instance, state_home=home),
        lambda: control.list_instances(state_home=home),
    ):
        with pytest.raises(RouterError, match="unsupported_store_version"):
            inspect()
    folder = workflow.parent.parent / "new-workflow"
    shutil.copytree(workflow.parent, folder)
    another_project = project.parent / "new-project"
    another_project.mkdir()
    refused = control.create_instance(
        folder / "workflow.json",
        another_project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    assert (
        refused["status"] == "refused"
        and refused["code"] == "unsupported_store_version"
    )
    assert _dump(db_path) == before
    assert len(list((home / "instances").glob("*/instance.json"))) == 1


@pytest.mark.parametrize("count", [2, 5, 3])
def test_dynamic_manifest_handoff_and_retry_aftermath(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    workflow, project, home = single_flow
    (project / "request.txt").write_text(str(count))
    scripts = workflow.parent / "scripts"
    (scripts / "produce.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "out=Path('out'); out.mkdir(exist_ok=True)\n"
        "counter=out/'producer-count'\n"
        "n=int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(n+1))\n"
        "paths=[]\n"
        "for i in range(int(Path('request.txt').read_text())):\n"
        " p=out/f'company-{i*i+17}.json'\n"
        " p.write_text(json.dumps({'score':i+1}))\n"
        " paths.append(str(p))\n"
        "(out/'manifest.json').write_text(json.dumps({'artifacts':paths}))\n"
        "print(json.dumps({'outcome':'READY','refs':['out/manifest.json',*paths]}))\n"
    )
    (scripts / "consume.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "paths=json.loads(Path('out/manifest.json').read_text())['artifacts']\n"
        "values=[json.loads(Path(p).read_text())['score'] for p in paths]\n"
        "assert values==list(range(1,len(paths)+1))\n"
        "once=Path('out/consumer-attempted')\n"
        "if not once.exists():\n once.write_text('checked'); sys.exit(7)\n"
        "Path('out/report.txt').write_text(str(sum(values)))\n"
        "print(json.dumps({'outcome':'READY','refs':['out/report.txt']}))\n"
    )
    graph = _graph(workflow)
    producer = _object(_object(graph["planes"]["main"]["nodes"])["draft"])
    producer["invoke"] = {
        "binding": "python",
        "script": "scripts/produce.py",
        "inputs": ["request.txt"],
        "timeout_seconds": 2,
    }
    producer["on"] = {"READY": {"goto": "consume"}}
    consumer = json.loads(command_graph_json(producer))
    consumer["invoke"] = {
        "binding": "python",
        "script": "scripts/consume.py",
        "inputs": ["out/manifest.json"],
        "timeout_seconds": 2,
    }
    consumer["on"] = {"READY": {"finish": "succeeded"}}
    consumer["on_error"] = {"pause": {"reason": "consumer_failed", "resume": "consume"}}
    graph["planes"]["main"]["nodes"] = {"draft": producer, "consume": consumer}
    workflow.write_text(command_graph_json(graph))
    instance = _register(single_flow)
    ids: dict[str, object] = {}
    complete = Store.complete

    class ProducerCommitted(BaseException):
        pass

    def commit_then_crash(
        store: Store, invocation_id: str, result: CommandResult
    ) -> None:
        complete(store, invocation_id, result)
        if (
            store.db.execute(
                "SELECT node FROM invocations WHERE id=?", (invocation_id,)
            ).fetchone()[0]
            == "draft"
        ):
            raise ProducerCommitted()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", commit_then_crash)
        with pytest.raises(ProducerCommitted):
            control.start_run(
                instance,
                state_home=home,
                request_id=new_id(),
                expected_state_version=1,
                on_started=ids.update,
            )
    run = str(ids["run_id"])
    committed = control.inspect_run(instance, run, state_home=home)
    assert committed["count"] == 1 and len(_items(committed["completions"])) == 1
    assert committed["transitions"] == []
    assert (project / "out/producer-count").read_text() == "1"
    manifest = _object(json.loads((project / "out/manifest.json").read_text()))
    refs = cast(list[str], manifest["artifacts"])
    artifact = project / refs[-1]
    artifact_bytes = artifact.read_bytes()
    if count != 2:
        # These cases fail on domain content/file checks, not the scripted exit7.
        (project / "out/consumer-attempted").write_text("checked")
        if count == 5:
            artifact.write_text(command_graph_json({"score": -1}))
        else:
            artifact.unlink()
    recovered = control.recover_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=_version(committed),
    )
    assert recovered["status"] == "applied", recovered
    paused = control.inspect_run(instance, run, state_home=home)
    assert paused["status"] == "paused" and paused["count"] == 2
    failed = _items(paused["completions"])[1]
    assert (
        failed["transport"] == "execution_failure"
        and failed["effective_writer"] == "stopped"
    )
    if count != 2:
        artifact.write_bytes(artifact_bytes)
    retry = control.retry_invocation(
        instance,
        run,
        invocation_id=str(_items(paused["invocations"])[1]["id"]),
        reason="consumer_checked",
        state_home=home,
        request_id=new_id(),
        expected_state_version=_version(paused),
        plane_id="main",
    )
    assert retry["status"] == "applied", retry
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "succeeded" and final["count"] == 3
    assert [item["node"] for item in _items(final["invocations"])] == [
        "draft",
        "consume",
        "consume",
    ]
    assert (project / "out/producer-count").read_text() == "1"
    assert (project / "out/report.txt").read_text() == str(count * (count + 1) // 2)
    assert final == control.inspect_run(instance, run, state_home=home)
    assert not (project / ".millrace-router-owner/owner.json").exists()


def test_valid_shadowed_timeout_still_binds_captured_run_authority(
    single_flow: tuple[Path, Path, Path],
) -> None:
    from tests.router.support import create_instance, start_run

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    run = start_run(instance, state_home=home)
    config = control.load_instance(instance, home)
    with sqlite3.connect(config.store_path) as db:
        body = json.loads(db.execute("SELECT body FROM runs").fetchone()[0])
        body["host_settings"]["runtime_defaults"]["timeout_seconds"] = 37
        db.execute("UPDATE runs SET body=?", (command_graph_json(body),))
    for _ in range(2):
        with pytest.raises(RouterError) as error:
            control.inspect_run(instance, str(run["id"]), state_home=home)
        assert error.value.code == "run_integrity"


def test_plane_defaults_use_declared_plane_identity(tmp_path: Path) -> None:
    from tests.router.test_planes import authored

    from millrace.router.graph import capture, resolve_nodes

    data = authored()
    data["limits"]["max_parallel_invocations"] = 1
    data["defaults"] = {"timeout_seconds": 12}
    for name, timeout in (("alpha", 7), ("beta", 9)):
        data["planes"][name]["workspace"] = "."
        data["planes"][name]["defaults"] = {"timeout_seconds": timeout}
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    path = workflow / "workflow.json"
    path.write_text(json.dumps(data))
    revision = capture(path, project)
    context = {
        "runtime_defaults": {"timeout_seconds": 20},
        "operator_policy": {"timeout_ceiling_seconds": 8},
        "state_version": 1,
        "path": [],
        "host_paths": {},
        "env_allowlist": [],
    }
    resolved = resolve_nodes(revision, settings_context=context, project_root=project)
    assert set(resolved) == {"alpha", "beta"}
    assert resolved["alpha"]["step"]["common"]["effective"]["timeout_seconds"] == 7
    assert resolved["beta"]["step"]["common"]["effective"]["timeout_seconds"] == 8
    for name in resolved:
        common = resolved[name]["step"]["common"]
        assert (
            common["plane_id"] == name
            and common["producing_revision"] == revision.revision_id
        )
        assert common["provenance"]["timeout_seconds"]["source"] == "plane"
    data["planes"]["alpha"]["nodes"]["step"]["invoke"]["timeout_seconds"] = 4
    path.write_text(json.dumps(data))
    selected = resolve_nodes(
        capture(path, project), settings_context=context, project_root=project
    )
    assert selected["alpha"]["step"]["common"]["effective"]["timeout_seconds"] == 4
    assert (
        selected["alpha"]["step"]["common"]["provenance"]["timeout_seconds"]["source"]
        == "node"
    )
    data["planes"]["alpha"]["defaults"]["timeout_seconds"] = 0
    path.write_text(json.dumps(data))
    with pytest.raises(RouterError, match="timeout_seconds"):
        capture(path, project)
