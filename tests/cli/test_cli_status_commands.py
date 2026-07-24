from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    from millrace.adapters.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _json(raw: str) -> dict[str, Any]:
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _compile_export(path: Path) -> str:
    from millrace.compiler import authority_fingerprint, compile_workflow
    from millrace.compiler.export import compiled_plan_export_bytes
    from millrace.workflows import kernel_ping

    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    path.write_bytes(compiled_plan_export_bytes(result.plan))
    return authority_fingerprint(result.plan)


def _state(workspace: Path):
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(workspace / ".millrace" / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(workspace / ".millrace" / "cas")
    return store.load_runtime_state(cas_store)


def _workspace_with_work(tmp_path: Path) -> tuple[Path, str, str, str]:
    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.export.json"
    fingerprint = _compile_export(export_path)
    commands = (
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-workspace",
        ],
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "admit",
            "--compiled-plan-json",
            str(export_path),
            "--input-id",
            "admit-plan",
        ],
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "select-default",
            fingerprint,
            "--input-id",
            "select-plan",
        ],
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            '{"body":"trace me"}',
            "--input-id",
            "enqueue-prompt",
        ],
    )
    activation_id = ""
    work_item_id = ""
    for args in commands:
        exit_code, stdout, stderr = _invoke(args)
        assert exit_code == 0, (stdout, stderr)
        payload = _json(stdout)
        if payload["command"] == "queue.enqueue":
            activation_id = str(payload["data"]["activation_id"])
            work_item_id = str(payload["data"]["work_item_id"])
    return workspace, fingerprint, work_item_id, activation_id


def test_status_and_list_commands_are_read_only(tmp_path: Path) -> None:
    workspace, _fingerprint, work_item_id, _activation_id = _workspace_with_work(
        tmp_path
    )
    before = _state(workspace)

    commands = (
        ["status"],
        ["queue", "list"],
        ["runs", "list"],
        ["waits", "list"],
        ["interventions", "list"],
        ["trace", "show"],
    )
    for command in commands:
        exit_code, stdout, stderr = _invoke(
            ["--json", "--workspace", str(workspace), *command]
        )
        assert exit_code == 0, (command, stdout, stderr)
        assert _json(stdout)["ok"] is True

    missing_code, missing_stdout, missing_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "runs",
            "show",
            work_item_id,
        ]
    )
    assert missing_code == 3
    assert missing_stdout == ""
    assert _json(missing_stderr)["code"] == "run_not_found"
    assert _state(workspace) == before


def test_negative_max_events_refuses_before_store_load(tmp_path: Path) -> None:
    workspace = tmp_path / "missing-workspace"

    for command in (["status"], ["trace", "show"]):
        exit_code, stdout, stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                *command,
                "--max-events",
                "-1",
            ]
        )
        assert exit_code == 3
        assert stdout == ""
        assert _json(stderr)["code"] == "invalid_max_events"
        assert not workspace.exists()


def test_status_max_events_validation_matches_operator_status(tmp_path: Path) -> None:
    workspace, _fingerprint, _work_item_id, _activation_id = _workspace_with_work(
        tmp_path
    )
    before = _state(workspace)

    for value in ("-1", "not-an-int"):
        exit_code, stdout, stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                "status",
                "--max-events",
                value,
            ]
        )
        assert exit_code == 2 if value == "not-an-int" else 3
        assert stdout == ""
        assert _state(workspace) == before

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "status",
            "--plan-fingerprint",
            "not-a-fingerprint",
        ]
    )
    assert exit_code == 3
    assert stdout == ""
    assert _json(stderr)["code"] == "invalid_plan_fingerprint"
    assert _state(workspace) == before

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "status",
            "--plan-fingerprint",
            f"sha256:{'0' * 64}",
        ]
    )
    assert exit_code == 0, (stdout, stderr)
    assert _json(stdout)["data"]["selected_plan"] is None
    assert _json(stdout)["data"]["queue_families"] == []
    assert _state(workspace) == before


def test_trace_show_supports_recent_and_run_specific_projection(tmp_path: Path) -> None:
    workspace, _fingerprint, _work_item_id, activation_id = _workspace_with_work(
        tmp_path
    )
    claim_code, claim_stdout, claim_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "claim",
            activation_id,
            "--input-id",
            "claim-work",
        ]
    )
    assert claim_code == 0, (claim_stdout, claim_stderr)
    run_id = str(_json(claim_stdout)["data"]["run_id"])
    before = _state(workspace)

    recent_code, recent_stdout, recent_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "trace",
            "show",
            "--max-events",
            "2",
        ]
    )
    assert recent_code == 0, (recent_stdout, recent_stderr)
    recent = _json(recent_stdout)["data"]["events"]
    assert len(recent) == 2

    run_code, run_stdout, run_stderr = _invoke(
        ["--json", "--workspace", str(workspace), "trace", "show", run_id]
    )
    assert run_code == 0, (run_stdout, run_stderr)
    run_events = _json(run_stdout)["data"]["events"]
    assert run_events != []
    assert {event["run_id"] for event in run_events} == {run_id}

    missing_code, missing_stdout, missing_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "trace",
            "show",
            "missing-run",
        ]
    )
    assert missing_code == 3
    assert missing_stdout == ""
    assert _json(missing_stderr)["code"] == "run_not_found"

    negative_code, negative_stdout, negative_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "trace",
            "show",
            "--max-events",
            "-1",
        ]
    )
    assert negative_code == 3
    assert negative_stdout == ""
    assert _json(negative_stderr)["code"] == "invalid_max_events"
    assert _state(workspace) == before
