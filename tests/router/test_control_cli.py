"""Caller-retained exact envelopes through the real CLI transport."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.router.support import command_graph_json

from millrace.router import control
from millrace.router.records import CommandResult
from millrace.router.store import new_id

CLI = sys.executable


def call(
    home: Path, *args: str, env: dict[str, str] | None = None
) -> list[dict[str, object]]:
    result = subprocess.run(
        [CLI, "-m", "millrace.router.cli", "--state-home", str(home), *args],
        text=True,
        capture_output=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_cli_retains_original_payload_and_version(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    created = call(
        home,
        "instance",
        "create",
        "--workflow",
        str(workflow),
        "--project",
        str(project),
    )
    original_create = created[0]["request_envelope"]
    receipt = created[-1]
    instance = str(receipt["instance_id"])
    changed_environment = {
        **os.environ,
        "PATH": "/usr/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert (
        call(
            home,
            "replay",
            "--envelope",
            command_graph_json(original_create),
            env=changed_environment,
        )[-1]
        == receipt
    )
    started = call(home, "start", "--instance", instance)
    start_envelope = started[0]["request_envelope"]
    start_receipt = started[-1]
    assert started[1]["run_id"] == start_receipt["run_id"]
    graph = json.loads(workflow.read_text())
    graph["limits"]["max_invocations"] = 3
    workflow.write_text(command_graph_json(graph))
    assert (
        call(
            home,
            "replay",
            "--envelope",
            command_graph_json(start_envelope),
            env=changed_environment,
        )[-1]
        == start_receipt
    )
    assert len(control.inspect_instance(instance, state_home=home)["runs"]) == 1
    source = project / "settings-input.json"
    source.write_text(command_graph_json({"env_allowlist": ["ORIGINAL_NAME"]}))
    configured = call(
        home,
        "instance",
        "config",
        "--instance",
        instance,
        "--settings",
        source.read_text(),
    )
    retained = configured[0]["request_envelope"]
    final = configured[-1]
    source.write_text(command_graph_json({"env_allowlist": ["NEW_NAME"]}))
    assert (
        call(
            home,
            "replay",
            "--envelope",
            command_graph_json(retained),
            env=changed_environment,
        )[-1]
        == final
    )
    current = control.inspect_instance(instance, state_home=home)
    assert (
        current["env_allowlist"] == ["ORIGINAL_NAME"]
        and current["state_version"] == final["state_version"]
    )


def test_cli_discovers_queued_then_final_same_request(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    created = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    instance = str(created["instance_id"])
    ids: dict[str, object] = {}
    retained: dict[str, object] = {}

    def invoke(*args: object) -> CommandResult:
        queued = call(
            home, "pause", "--instance", instance, "--run", str(ids["run_id"])
        )
        retained.update(queued[0]["request_envelope"])
        assert queued[-1]["status"] == "queued"
        assert (
            call(home, "replay", "--envelope", command_graph_json(retained))[-1]
            == queued[-1]
        )
        args[4]()
        return CommandResult(
            b'{"outcome":"READY"}', b"", "completed", "stopped", exit_code=0
        )

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    start = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        on_started=ids.update,
    )
    paused = control.inspect_run(instance, str(start["run_id"]), state_home=home)
    assert paused["status"] == "paused" and paused["count"] == 1
    resolved = call(home, "replay", "--envelope", command_graph_json(retained))[-1]
    assert resolved["status"] == "applied" and resolved["action"] == "paused"
    assert (
        call(home, "replay", "--envelope", command_graph_json(retained))[-1] == resolved
    )
    assert (
        control.inspect_run(instance, str(start["run_id"]), state_home=home) == paused
    )


def test_replay_requires_home_and_explicit_empty_id_refuses(single_flow):
    workflow, project, home = single_flow
    created = call(
        home,
        "instance",
        "create",
        "--workflow",
        str(workflow),
        "--project",
        str(project),
    )
    request = created[0]["request_envelope"]
    result = subprocess.run(
        [
            CLI,
            "-m",
            "millrace.router.cli",
            "replay",
            "--envelope",
            command_graph_json(request),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2 and "state_home_required" in result.stderr
    result = subprocess.run(
        [
            CLI,
            "-m",
            "millrace.router.cli",
            "--state-home",
            str(home),
            "start",
            "--instance",
            str(created[-1]["instance_id"]),
            "--request-id",
            "",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2 and "invalid_id" in result.stderr
    assert (
        control.inspect_instance(str(created[-1]["instance_id"]), state_home=home)[
            "runs"
        ]
        == []
    )


def test_cli_selected_settings_context_and_fallback_warning(single_flow):
    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["defaults"] = {"binding": "python"}
    del graph["planes"]["main"]["nodes"]["draft"]["invoke"]["binding"]
    del graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"]
    workflow.write_text(command_graph_json(graph))
    instance = str(
        call(
            home,
            "instance",
            "create",
            "--workflow",
            str(workflow),
            "--project",
            str(project),
        )[-1]["instance_id"]
    )
    warning = {
        "code": "timeout_defaulted",
        "plane_id": "main",
        "node_id": "draft",
        "field": "timeout_seconds",
    }
    before = call(home, "validate", "--instance", instance)[-1]
    assert before["valid"] is True and before["warnings"] == [warning]
    assert before["validation_context"] == {
        "kind": "instance",
        "instance_id": instance,
        "run_id": None,
        "settings_version": 1,
    }
    started = call(home, "start", "--instance", instance)[-1]
    run = str(started["run_id"])
    terminal = control.inspect_run(instance, run, state_home=home)
    assert terminal["status"] == "succeeded"
    frozen = terminal["invocations"][0]["launch_settings"]["common"]
    assert frozen == {
        **before["resolved_nodes"]["main"]["draft"]["common"],
        "producing_revision": terminal["invocations"][0]["revision_id"],
    }
    configured = call(
        home,
        "instance",
        "config",
        "--instance",
        instance,
        "--settings",
        command_graph_json(
            {
                "runtime_defaults": {"timeout_seconds": 7},
                "operator_policy": {"timeout_ceiling_seconds": 4},
            }
        ),
    )[-1]
    current = call(home, "validate", "--instance", instance)[-1]
    assert current["valid"] is True and current["warnings"] == []
    assert (
        current["validation_context"]["settings_version"] == configured["state_version"]
    )
    launch = current["resolved_nodes"]["main"]["draft"]["common"]
    assert launch["effective"]["timeout_seconds"] == 4
    assert launch["requested"]["timeout_seconds"] == 7
    historical = call(home, "validate", "--instance", instance, "--run", run)[-1]
    assert historical["validation_context"] == {
        "kind": "run",
        "instance_id": instance,
        "run_id": run,
        "settings_version": 1,
        "run_state_version": terminal["state_version"],
    }
    assert historical["warnings"] == [warning]
    assert historical["resolved_nodes"]["main"]["draft"]["common"] == frozen
    assert control.inspect_run(instance, run, state_home=home) == terminal
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["unknown"] = True
    workflow.write_text(command_graph_json(graph))
    invalid = call(home, "validate", "--instance", instance)[-1]
    assert invalid["valid"] is False and invalid["diagnostics"] == ["invalid_fields"]
    assert invalid["resolved_nodes"] == {} and invalid["warnings"] == []
    assert invalid["validation_context"] == current["validation_context"]
    reloaded = control.inspect_run(instance, run, state_home=home)
    assert reloaded["invocations"] == terminal["invocations"]
    assert reloaded["draft_revision"]["diagnostic"] == "invalid_fields"


@pytest.mark.parametrize(
    "command,extra",
    [
        ("pause-plane", []),
        ("resume-plane", []),
        ("cancel-plane", []),
        ("redirect-plane", ["--node", "step", "--reason", "operator target"]),
        ("retry", ["--reason", "retry selected invocation"]),
        ("reconcile", ["--reason", "confirmed stopped", "--stopped"]),
    ],
)
def test_cli_scoped_controls_match_real_core(tmp_path, command, extra):
    """Real command runtime and exact replay share receipts and plane projection."""
    from tests.router.test_planes import authored

    graph = authored()
    graph["seams"] = {}
    graph["bindings"]["python"]["executable"] = sys.executable
    for name in ("alpha", "beta"):
        plane = graph["planes"][name]
        node = plane["nodes"]["step"]
        node["invoke"]["config"]["args"] = ["-c", 'print(\'{"outcome":"READY"}\')']
        node["on"]["READY"] = {"pause": {"reason": "checked", "resume": "step"}}
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps(graph))
    project = tmp_path / "project"
    project.mkdir()
    for name in ("alpha", "beta"):
        (project / name).mkdir()
    home = tmp_path / "state"
    created = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
    )
    assert created["status"] == "applied", created
    instance = str(created["instance_id"])
    start = call(home, "start", "--instance", instance)
    assert start[0]["request_envelope"]["run_id"] is None
    assert start[0]["request_envelope"]["plane_id"] is None
    run = str(start[-1]["run_id"])
    before = control.inspect_run(instance, run, state_home=home)
    assert set(before["planes"]) == {"alpha", "beta"}
    assert before["status"] == "paused"
    if command in {"retry", "reconcile"}:
        invocation = next(
            item for item in before["invocations"] if item["plane_id"] == "alpha"
        )
        extra = [*extra, "--invocation", str(invocation["id"])]
    result = subprocess.run(
        [
            CLI,
            "-m",
            "millrace.router.cli",
            "--state-home",
            str(home),
            command,
            "--instance",
            instance,
            "--run",
            run,
            "--plane",
            "alpha",
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode in {0, 2}, result.stderr
    output = [json.loads(line) for line in result.stdout.splitlines()]
    request, receipt = output[0]["request_envelope"], output[-1]
    assert request["plane_id"] == "alpha" and request["run_id"] == run
    after = control.inspect_run(instance, run, state_home=home)
    assert receipt == control.execute_request(request, state_home=home)
    assert control.inspect_run(instance, run, state_home=home) == after
    assert call(home, "status", "--instance", instance, "--run", run)[-1] == after
    assert after["planes"]["beta"] == before["planes"]["beta"]
    if command == "reconcile":
        assert receipt["status"] == "refused" and result.returncode == 2
        assert json.loads(result.stderr)["code"] == receipt["code"]
    else:
        assert receipt["status"] == "applied" and result.returncode == 0, receipt
