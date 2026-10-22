"""The active CLI imports and executes the generic router directly."""

import json
import os
import subprocess
import sys

import pytest
from tests.router.test_execution_boundary import generic_flow


def test_generic_router_cli_uses_shared_controls(tmp_path):
    file, project, home = generic_flow(tmp_path)

    def call(*args):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "millrace.router.cli",
                "--state-home",
                str(home),
                *args,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        return [json.loads(line) for line in result.stdout.splitlines()]

    created = call(
        "instance", "create", "--workflow", str(file), "--project", str(project)
    )[-1]
    instance = created["instance_id"]
    validation = call("validate", "--instance", instance)[-1]
    started = call("start", "--instance", instance)
    final = call("status", "--instance", instance, "--run", started[-1]["run_id"])[-1]
    assert final["status"] == "succeeded"
    advisory = validation["resolved_nodes"]["main"]["step"]
    # Advisory capture has lexical workspace identities; launch binds the live revision.
    assert final["invocations"][0]["launch_settings"] == {
        **advisory,
        "common": {
            **advisory["common"],
            "producing_revision": final["invocations"][0]["revision_id"],
        },
    }
    assert (
        call("replay", "--envelope", json.dumps(started[0]["request_envelope"]))[-1]
        == started[-1]
    )


def test_cli_create_accepts_generic_host_aliases(tmp_path):
    file, project, home = generic_flow(tmp_path)
    graph = json.loads(file.read_text())
    graph["bindings"]["python"]["executable"] = "runtime"
    file.write_text(json.dumps(graph))
    aliases = {"runtime": sys.executable, "resource": str(project)}

    def call(*args):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "millrace.router.cli",
                "--state-home",
                str(home),
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return [json.loads(line) for line in result.stdout.splitlines()]

    created = call(
        "instance",
        "create",
        "--workflow",
        str(file),
        "--project",
        str(project),
        "--host-paths",
        json.dumps(aliases),
    )
    assert created[0]["request_envelope"]["payload"]["host_paths"] == aliases
    instance = created[-1]["instance_id"]
    from millrace.router.control import load_instance

    assert load_instance(instance, home).host_paths == aliases
    assert call("validate", "--instance", instance)[-1]["valid"]
    started = call("start", "--instance", instance)[-1]
    assert (
        call("status", "--instance", instance, "--run", started["run_id"])[-1]["status"]
        == "succeeded"
    )


@pytest.mark.parametrize(
    "raw,code",
    [
        ("{", "invalid_json"),
        ("[]", "invalid_shape"),
        ('{"runtime":1}', "invalid_string"),
        ('{"runtime":{}}', "invalid_string"),
        ('{"runtime":""}', "invalid_string"),
        ('{"runtime":"a","runtime":"b"}', "duplicate_key"),
    ],
)
def test_cli_invalid_host_paths_do_not_create_instance(tmp_path, raw, code):
    file, project, home = generic_flow(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "millrace.router.cli",
            "--state-home",
            str(home),
            "instance",
            "create",
            "--workflow",
            str(file),
            "--project",
            str(project),
            "--host-paths",
            raw,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["code"] == code
    assert not result.stdout and not home.exists()


# Transport-only proofs exercise parsing and forwarding, not runtime success.
@pytest.mark.parametrize(
    "command,operation,extra,payload,plane,version",
    [
        ("start", "start_run", ["--edit-mode", "live"], {"edit_mode": "live"}, None, 7),
        ("recover", "recover_run", [], {}, None, 7),
        ("pause", "pause_run", [], {}, None, None),
        ("cancel", "cancel_run", [], {}, None, None),
        ("resume", "resume_run", [], {}, None, 7),
        ("submit", "submit_revision", [], {}, None, 7),
        (
            "accept",
            "accept_revision",
            ["--candidate", "a" * 64],
            {"candidate_id": "a" * 64},
            None,
            7,
        ),
        (
            "retry",
            "retry_invocation",
            ["--invocation", "a" * 32, "--reason", "retry"],
            {"invocation_id": "a" * 32, "reason": "retry"},
            "worker",
            7,
        ),
        (
            "reconcile",
            "reconcile_invocation",
            ["--invocation", "a" * 32, "--reason", "checked", "--stopped"],
            {"invocation_id": "a" * 32, "reason": "checked", "stopped": True},
            "worker",
            7,
        ),
        ("pause-plane", "pause_plane", [], {}, "worker", None),
        ("resume-plane", "resume_plane", [], {}, "worker", 7),
        ("cancel-plane", "cancel_plane", [], {}, "worker", None),
        (
            "redirect-plane",
            "redirect_plane",
            ["--node", "repair", "--reason", "repair"],
            {"node_id": "repair", "reason": "repair"},
            "worker",
            7,
        ),
        (
            "instance",
            "update_instance_config",
            ["config", "--settings", '{"env_allowlist":[]}'],
            {"settings": {"env_allowlist": []}},
            None,
            7,
        ),
    ],
)
def test_cli_v2_command_target_mapping(
    tmp_path, monkeypatch, capsys, command, operation, extra, payload, plane, version
):
    from millrace.router import cli

    seen = []
    reads = []
    resolver = object()

    def inspect(*args, **kwargs):
        reads.append((args, kwargs))
        return {"state_version": 7}

    def execute(request, **kwargs):
        seen.append((request, kwargs))
        kwargs["on_started"]({"run_id": "generated", "instance_id": "1" * 32})
        return {"status": "queued", "request_id": "b" * 32, "plane_id": plane}

    monkeypatch.setattr(cli.control, "execute_request", execute)
    monkeypatch.setattr(cli.control, "inspect_instance", inspect)
    monkeypatch.setattr(cli.control, "inspect_run", inspect)
    argv = [command, *extra, "--instance", "1" * 32, "--request-id", "b" * 32]
    if operation not in {"start_run", "update_instance_config"}:
        argv += ["--run", "2" * 32]
    if plane is not None:
        argv += ["--plane", plane]
    assert (
        cli.main(["--state-home", str(tmp_path), *argv], adapter_resolver=resolver) == 0
    )
    expected = {
        "format": "millrace-router-request/v2",
        "operation": operation,
        "instance_id": "1" * 32,
        "run_id": None
        if operation in {"start_run", "update_instance_config"}
        else "2" * 32,
        "plane_id": plane,
        "request_id": "b" * 32,
        "expected_state_version": version,
        "payload": payload,
    }
    assert seen[0][0] == expected
    assert reads == (
        []
        if version is None
        else [
            (
                ("1" * 32,) if expected["run_id"] is None else ("1" * 32, "2" * 32),
                {"state_home": tmp_path, "adapter_resolver": resolver},
            )
        ]
    )
    assert seen[0][1]["state_home"] == tmp_path
    assert seen[0][1]["adapter_resolver"] is resolver
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {"request_envelope": expected},
        {"run_id": "generated", "instance_id": "1" * 32},
        {"status": "queued", "request_id": "b" * 32, "plane_id": plane},
    ]


@pytest.mark.parametrize(
    "command,extra",
    [
        ("pause-plane", []),
        ("resume-plane", []),
        ("cancel-plane", []),
        ("redirect-plane", ["--node", "next", "--reason", "move"]),
        ("retry", ["--invocation", "a" * 32, "--reason", "retry"]),
        ("reconcile", ["--invocation", "a" * 32, "--reason", "checked", "--stopped"]),
    ],
)
def test_cli_scoped_commands_require_plane(
    tmp_path, monkeypatch, capsys, command, extra
):
    from millrace.router import cli

    def unexpected(*args, **kwargs):
        pytest.fail("missing target must refuse before calling Core")

    monkeypatch.setattr(cli.control, "execute_request", unexpected)
    monkeypatch.setattr(cli.control, "inspect_run", unexpected)
    assert (
        cli.main(
            [
                "--state-home",
                str(tmp_path),
                command,
                "--instance",
                "1" * 32,
                "--run",
                "2" * 32,
                *extra,
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)["code"] == "usage_error"
    assert "--plane" in json.loads(output.err)["message"]


@pytest.mark.parametrize(
    "argv",
    [
        [
            "redirect",
            "--instance",
            "1" * 32,
            "--run",
            "2" * 32,
            "--node",
            "next",
            "--reason",
            "move",
        ],
        ["start", "--instance", "1" * 32, "--run", "2" * 32],
        *[
            [command, "--instance", "1" * 32, "--run", "2" * 32, "--plane", "worker"]
            for command in ("pause", "cancel", "resume", "recover", "submit")
        ],
        *[
            [
                command,
                "--instance",
                "1" * 32,
                "--run",
                "2" * 32,
                "--expected-state-version",
                "7",
            ]
            for command in ("pause", "cancel")
        ],
        *[
            [
                command,
                "--instance",
                "1" * 32,
                "--run",
                "2" * 32,
                "--plane",
                "worker",
                "--expected-state-version",
                "7",
            ]
            for command in ("pause-plane", "cancel-plane")
        ],
    ],
)
def test_cli_refuses_ambiguous_or_inapplicable_scope(
    tmp_path, monkeypatch, capsys, argv
):
    from millrace.router import cli

    monkeypatch.setattr(
        cli.control,
        "execute_request",
        lambda *a, **k: pytest.fail("must refuse before dispatch"),
    )
    assert cli.main(["--state-home", str(tmp_path), *argv]) == 2
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)["code"] == "usage_error"


@pytest.mark.parametrize(
    "command", ["status", "validate", "instance-inspect", "instance-list"]
)
def test_cli_read_projection_is_unchanged(tmp_path, monkeypatch, capsys, command):
    from millrace.router import cli

    projection = {
        "planes": {"worker": {"status": "paused"}},
        "validation_context": {"run_state_version": 19},
    }
    seen = []
    resolver = object()

    def read(*args, **kwargs):
        seen.append((args, kwargs))
        return projection

    public = {
        "status": "inspect_run",
        "validate": "validate_revision",
        "instance-inspect": "inspect_instance",
        "instance-list": "list_instances",
    }[command]
    monkeypatch.setattr(cli.control, public, read)
    argv = (
        ["instance", command.removeprefix("instance-")]
        if command.startswith("instance-")
        else [command]
    )
    if command != "instance-list":
        argv += ["--instance", "1" * 32]
    if command in {"status", "validate"}:
        argv += ["--run", "2" * 32]
    if command == "validate":
        argv += ["--workflow", str(tmp_path / "draft.json")]
    assert (
        cli.main(["--state-home", str(tmp_path), *argv], adapter_resolver=resolver) == 0
    )
    assert json.loads(capsys.readouterr().out) == projection
    expected = {"state_home": tmp_path, "adapter_resolver": resolver}
    if command == "validate":
        expected.update(run_id="2" * 32, workflow_file=tmp_path / "draft.json")
    assert seen == [
        (
            ("1" * 32, "2" * 32)
            if command == "status"
            else ()
            if command == "instance-list"
            else ("1" * 32,),
            expected,
        )
    ]


@pytest.mark.parametrize("command", ["create", "release"])
def test_cli_special_envelopes_are_closed_v2(tmp_path, monkeypatch, capsys, command):
    from millrace.router import cli
    from millrace.router.control_requests import validate_envelope

    seen = []
    monkeypatch.setattr(
        cli.control,
        "execute_request",
        lambda request, **kwargs: seen.append(request) or {"status": "queued"},
    )
    argv = (
        [
            "instance",
            "create",
            "--workflow",
            str(tmp_path / "workflow.json"),
            "--project",
            str(tmp_path),
        ]
        if command == "create"
        else [
            "release-project-hold",
            "--project",
            str(tmp_path),
            "--hold",
            "c" * 32,
            "--owner-instance",
            "1" * 32,
            "--owner-run",
            "2" * 32,
            "--reason",
            "checked",
            "--stopped",
        ]
    )
    assert (
        cli.main(
            ["--state-home", str(tmp_path / "state"), *argv, "--request-id", "b" * 32]
        )
        == 0
    )
    request = seen[0]
    assert set(request) == {
        "format",
        "operation",
        "instance_id",
        "run_id",
        "plane_id",
        "request_id",
        "expected_state_version",
        "payload",
    }
    assert (
        request["format"] == "millrace-router-request/v2"
        and request["plane_id"] is None
    )
    assert request["run_id"] == (None if command == "create" else "2" * 32)
    assert request["expected_state_version"] == (0 if command == "create" else None)
    assert validate_envelope(request, creation=command == "create") == request
    assert json.loads(capsys.readouterr().out.splitlines()[0]) == {
        "request_envelope": request
    }


def test_cli_replay_forwards_exact_v2_and_refusal(tmp_path, monkeypatch, capsys):
    from millrace.router import cli

    request = {
        "format": "millrace-router-request/v2",
        "operation": "pause_plane",
        "instance_id": "1" * 32,
        "run_id": "2" * 32,
        "plane_id": "worker",
        "request_id": "b" * 32,
        "expected_state_version": None,
        "payload": {},
    }
    seen = []
    refusal = {"status": "refused", "code": "plane_not_found", "plane_id": "worker"}
    monkeypatch.setattr(
        cli.control,
        "execute_request",
        lambda value, **kwargs: seen.append(value) or refusal,
    )
    assert (
        cli.main(
            ["--state-home", str(tmp_path), "replay", "--envelope", json.dumps(request)]
        )
        == 2
    )
    output = capsys.readouterr()
    assert seen == [request]
    assert [json.loads(line) for line in output.out.splitlines()] == [
        {"request_envelope": request},
        refusal,
    ]
    assert json.loads(output.err) == {
        "code": "plane_not_found",
        "message": "plane_not_found",
    }


@pytest.mark.parametrize(
    "command,extra",
    [
        ("start", []),
        ("resume", ["--run", "2" * 32]),
        ("resume-plane", ["--run", "2" * 32, "--plane", "worker"]),
        ("instance", ["config", "--settings", "{}"]),
    ],
)
def test_cli_explicit_version_never_refreshes(
    tmp_path, monkeypatch, capsys, command, extra
):
    from millrace.router import cli

    def unexpected(*args, **kwargs):
        pytest.fail("explicit version must not silently refresh operator intent")

    seen = []
    monkeypatch.setattr(cli.control, "inspect_instance", unexpected)
    monkeypatch.setattr(cli.control, "inspect_run", unexpected)
    monkeypatch.setattr(
        cli.control,
        "execute_request",
        lambda request, **kwargs: seen.append(request) or {"status": "queued"},
    )
    assert (
        cli.main(
            [
                "--state-home",
                str(tmp_path),
                command,
                *extra,
                "--instance",
                "1" * 32,
                "--expected-state-version",
                "3",
            ]
        )
        == 0
    )
    assert seen[0]["expected_state_version"] == 3
    assert len(seen[0]["request_id"]) == 32
    assert (
        json.loads(capsys.readouterr().out.splitlines()[0])["request_envelope"]
        == seen[0]
    )


@pytest.mark.parametrize(
    "code", ["invalid_target", "stale_state_version", "writers_not_confirmed"]
)
def test_cli_core_errors_keep_exit_code(tmp_path, monkeypatch, capsys, code):
    from millrace.router import cli
    from millrace.router.records import RouterError

    def refuse(*args, **kwargs):
        raise RouterError(code)

    monkeypatch.setattr(cli.control, "execute_request", refuse)
    assert (
        cli.main(
            [
                "--state-home",
                str(tmp_path),
                "pause-plane",
                "--instance",
                "1" * 32,
                "--run",
                "2" * 32,
                "--plane",
                "worker",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["request_envelope"]["plane_id"] == "worker"
    assert json.loads(output.err) == {"code": code, "message": code}
