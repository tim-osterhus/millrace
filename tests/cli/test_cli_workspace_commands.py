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


def _directory_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(candidate.relative_to(path)): candidate.read_bytes()
        for candidate in sorted(path.rglob("*"))
        if candidate.is_file()
    }


def test_workspace_init_creates_store_with_control_transition_only(
    tmp_path: Path,
) -> None:
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    workspace = tmp_path / "workspace"

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-workspace",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    payload = _json(stdout)
    assert list(payload) == ["ok", "command", "code", "message", "data"]
    assert payload["command"] == "workspace.init"
    assert payload["code"] == "workspace_initialized"
    data = payload["data"]
    assert list(data) == [
        "workspace_path",
        "db_path",
        "cas_path",
        "schema_version",
        "initialized",
        "transition_disposition",
        "input_id",
    ]
    assert data["workspace_path"] == str(workspace)
    assert data["db_path"] == str(workspace / ".millrace" / "runtime.sqlite3")
    assert data["cas_path"] == str(workspace / ".millrace" / "cas")
    assert data["initialized"] is True
    assert data["transition_disposition"] == "accepted"
    assert data["input_id"] == "init-workspace"

    store = SQLiteRuntimeStore.open(workspace / ".millrace" / "runtime.sqlite3")
    state = store.load_runtime_state(
        ContentAddressedByteStore(workspace / ".millrace" / "cas")
    )
    assert state.admitted_plans == {}
    assert state.default_plan_ref is None
    assert state.work_items == {}
    assert state.activations == {}
    assert state.runs == {}
    assert tuple(state.receipts) == ("init-workspace",)
    assert len(state.transitions) == 1
    assert state.transitions[0].input_kind == "control.initialize_workspace"
    assert store.load_workflow_package_registry(
        ContentAddressedByteStore(workspace / ".millrace" / "cas")
    ).records == ()


def test_workspace_check_is_read_only_and_refuses_missing_store(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "missing-workspace"

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "check",
        ]
    )

    assert exit_code == 4
    assert stdout == ""
    payload = _json(stderr)
    assert payload == {
        "ok": False,
        "command": "workspace.check",
        "code": "store_not_initialized",
        "message": "SQLite runtime store is not initialized.",
        "details": {
            "db_path": str(workspace / ".millrace" / "runtime.sqlite3"),
            "cas_path": str(workspace / ".millrace" / "cas"),
        },
    }
    assert not workspace.exists()


def test_workspace_check_reports_initialized_store_without_writing(
    tmp_path: Path,
) -> None:
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    workspace = tmp_path / "workspace"
    _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-workspace",
        ]
    )
    store = SQLiteRuntimeStore.open(workspace / ".millrace" / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(workspace / ".millrace" / "cas")
    before = store.load_runtime_state(cas_store)

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "check",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    payload = _json(stdout)
    assert payload["command"] == "workspace.check"
    assert payload["code"] == "workspace_ok"
    assert payload["data"] == {
        "workspace_path": str(workspace),
        "db_path": str(workspace / ".millrace" / "runtime.sqlite3"),
        "cas_path": str(workspace / ".millrace" / "cas"),
        "schema_version": 7,
        "initialized": True,
        "admitted_plan_count": 0,
        "default_plan_fingerprint": None,
        "receipt_count": 1,
    }
    after = store.load_runtime_state(cas_store)
    assert after == before


def test_workspace_check_maps_exact_v6_to_upgrade_required_json_and_human(
    tmp_path: Path,
) -> None:
    import sqlite3

    workspace = tmp_path / "workspace"
    _invoke(
        [
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-workspace",
        ]
    )
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE store_metadata SET store_schema_version = 6 WHERE id = 1"
        )
    before = _directory_bytes(workspace)

    json_exit, json_stdout, json_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "check",
        ]
    )
    human_exit, human_stdout, human_stderr = _invoke(
        [
            "--workspace",
            str(workspace),
            "workspace",
            "check",
        ]
    )

    assert json_exit == human_exit == 4
    assert json_stdout == human_stdout == ""
    assert _json(json_stderr) == {
        "ok": False,
        "command": "workspace.check",
        "code": "workspace_upgrade_required",
        "message": "Workspace schema upgrade is required.",
        "details": {
            "current_schema_version": 6,
            "required_schema_version": 7,
        },
    }
    assert (
        human_stderr
        == "workspace_upgrade_required: Workspace schema upgrade is required.\n"
    )
    assert _directory_bytes(workspace) == before


def test_workspace_init_conflicting_input_id_returns_domain_refusal(
    tmp_path: Path,
) -> None:
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.json"
    _compile_export(export_path)
    init_code, init_stdout, init_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-workspace",
        ]
    )
    assert init_code == 0, (init_stdout, init_stderr)

    admit_code, admit_stdout, admit_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "admit",
            "--compiled-plan-json",
            str(export_path),
            "--input-id",
            "same-control-id",
        ]
    )
    assert admit_code == 0, (admit_stdout, admit_stderr)
    before = SQLiteRuntimeStore.open(
        workspace / ".millrace" / "runtime.sqlite3"
    ).load_runtime_state(ContentAddressedByteStore(workspace / ".millrace" / "cas"))

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "same-control-id",
        ]
    )

    assert exit_code == 3
    assert stdout == ""
    payload = _json(stderr)
    assert payload["command"] == "workspace.init"
    assert payload["code"] == "idempotency_conflict"
    after = SQLiteRuntimeStore.open(
        workspace / ".millrace" / "runtime.sqlite3"
    ).load_runtime_state(ContentAddressedByteStore(workspace / ".millrace" / "cas"))
    assert after == before
