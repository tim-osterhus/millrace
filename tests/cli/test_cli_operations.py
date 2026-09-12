from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest
from tests.substrate.test_control_operations import public_runtime_target, request_for

from millrace.adapters.cli.main import main
from millrace.substrate.sqlite import SQLiteRuntimeStore


def invoke(workspace: Path, *args: str) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = main(["--json", "--workspace", str(workspace), *args])
    return code, json.loads(output.getvalue())


def test_public_identity_lookup_and_seal(tmp_path: Path) -> None:
    code, initialized = invoke(tmp_path, "workspace", "init", "--input-id", "init")
    assert code == 0
    db = tmp_path / ".millrace/runtime.sqlite3"
    before = db.read_bytes()
    code, discovered = invoke(tmp_path, "workspace", "identity")
    assert code == 0
    assert discovered["data"]["identity"] == initialized["data"]["identity"]
    store = SQLiteRuntimeStore.open(db)
    request = request_for(store)
    store.close()
    code, observed = invoke(
        tmp_path, "operations", "show", "--request-json", request.canonical
    )
    assert code == 0
    assert observed["data"]["status"] == "not_found_at_revision"
    assert observed["data"]["nonacceptance_proven"] is False
    assert db.read_bytes() == before
    code, sealed = invoke(
        tmp_path,
        "operations",
        "resolve",
        "--seal-if-absent",
        "--request-json",
        request.canonical,
    )
    assert code == 0
    assert sealed["data"]["receipt"]["disposition"] == "sealed_not_accepted"
    _, replay = invoke(
        tmp_path, "operations", "show", "--request-json", request.canonical
    )
    assert replay["data"]["replayed"] is True
    assert replay["data"]["receipt"] == sealed["data"]["receipt"]


@pytest.mark.parametrize("long_sessions", [False, True])
def test_public_long_runtime_reference_lookup_seal_and_conflict(
    tmp_path: Path, long_sessions: bool
) -> None:
    assert invoke(tmp_path, "workspace", "init", "--input-id", "init")[0] == 0
    db = tmp_path / ".millrace/runtime.sqlite3"
    store = SQLiteRuntimeStore.open(db)
    target = public_runtime_target(store)
    if long_sessions:
        target["expected_session"].update(
            session_id="session-" + "é" * 128,
            session_fencing_token="session-fence-" + "雪" * 128,
        )
    request = request_for(store, target=target)
    store.close()
    before = db.read_bytes()
    code, observed = invoke(
        tmp_path, "operations", "show", "--request-json", request.canonical
    )
    assert code == 0
    assert observed["data"]["status"] == "not_found_at_revision"
    assert db.read_bytes() == before
    code, sealed = invoke(
        tmp_path,
        "operations",
        "resolve",
        "--seal-if-absent",
        "--request-json",
        request.canonical,
    )
    assert code == 0
    receipt = sealed["data"]["receipt"]
    assert receipt["disposition"] == "sealed_not_accepted"
    assert receipt["target"] == target
    assert receipt["request_digest"] == request.digest
    code, replay = invoke(
        tmp_path, "operations", "show", "--request-json", request.canonical
    )
    assert code == 0
    assert replay["data"]["replayed"] is True
    assert replay["data"]["receipt"] == receipt
    changed = request.payload
    changed["target"]["run_fencing_token"] += ":changed"
    code, conflict = invoke(
        tmp_path,
        "operations",
        "resolve",
        "--seal-if-absent",
        "--request-json",
        json.dumps(changed),
    )
    assert code == 3
    assert conflict["code"] == "operation_idempotency_conflict"


def test_invalid_public_input_is_redacted_and_does_not_initialize(
    tmp_path: Path,
) -> None:
    code, result = invoke(
        tmp_path, "operations", "show", "--request-json", '{"secret":"credential"}'
    )
    assert code == 2
    assert result["details"]["receipt_persisted"] is False
    assert "credential" not in json.dumps(result)
    assert not (tmp_path / ".millrace").exists()


def test_no_successful_run_control_surface(tmp_path: Path) -> None:
    for action in ("pause", "resume"):
        code, _ = invoke(tmp_path, "runs", action, "run-a")
        assert code == 2
