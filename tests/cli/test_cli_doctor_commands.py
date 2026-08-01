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


def _workspace_with_ready_work(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.export.json"
    fingerprint = _compile_export(export_path)
    for args in (
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
            '{"body":"doctor should not mutate"}',
            "--input-id",
            "enqueue-prompt",
        ],
    ):
        exit_code, stdout, stderr = _invoke(args)
        assert exit_code == 0, (stdout, stderr)
    return workspace, fingerprint


def test_doctor_is_minimal_generic_read_only_projection(tmp_path: Path) -> None:
    workspace, fingerprint = _workspace_with_ready_work(tmp_path)
    before = _state(workspace)

    exit_code, stdout, stderr = _invoke(
        ["--json", "--workspace", str(workspace), "doctor"]
    )

    assert exit_code == 0, (stdout, stderr)
    payload = _json(stdout)
    assert payload["command"] == "doctor"
    assert payload["code"] == "doctor_ok"
    data = payload["data"]
    assert list(data) == [
        "workspace",
        "store",
        "cas",
        "default_plan",
        "packages",
        "ready_dispatch",
        "dispatch_suspension",
        "queue_closures",
        "daemon_budgets",
        "runner_session_mechanics",
        "runner_session_diagnostics",
        "required_paths",
    ]
    assert data["daemon_budgets"] == []
    assert data["workspace"]["path"] == str(workspace)
    assert data["store"]["initialized"] is True
    assert data["cas"]["initialized"] is True
    assert data["default_plan"]["authority_fingerprint"] == fingerprint
    assert data["packages"]["registered_count"] == 0
    assert data["ready_dispatch"]["candidate_count"] == 1
    assert data["ready_dispatch"]["severity_counts"] == {}
    assert data["runner_session_mechanics"] == {
        "cooperative_cancel_grace_seconds": 5.0,
        "terminate_grace_seconds": 5.0,
    }
    assert data["runner_session_diagnostics"] == {
        "diagnostic_counts": {},
        "diagnostics": [],
    }
    assert data["required_paths"]["db_path"]["exists"] is True
    assert data["required_paths"]["cas_path"]["exists"] is True
    assert "watcher" not in json.dumps(data)
    assert "mailbox" not in json.dumps(data)
    assert "reload_config" not in json.dumps(data)
    assert _state(workspace) == before


def test_runner_session_doctor_diagnostics_are_bounded(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from millrace.adapters.cli import doctor

    sessions = {
        f"session-{index}": SimpleNamespace(
            session_id=f"session-{index}",
            run_id=f"run-{index}",
            state="lost",
            cleanup_disposition="orphan_risk",
        )
        for index in range(75)
    }
    state = SimpleNamespace(
        runner_sessions=sessions,
        runner_session_completions={},
    )
    monkeypatch.setattr(
        doctor,
        "runner_session_projection",
        lambda _state, run_id: {
            "session_id": run_id.replace("run-", "session-"),
            "run_id": run_id,
        },
    )

    projected = doctor._runner_session_diagnostics(state)

    assert projected["diagnostic_counts"] == {
        "runner_session_lost": 75,
        "runner_session_orphan_risk": 75,
    }
    assert len(projected["diagnostics"]) == 100
    assert projected["truncation"] == {
        "retained_count": 100,
        "omitted_count": 50,
    }
